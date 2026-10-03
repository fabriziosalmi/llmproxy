"""Post-flight plugins state their failure direction, and mean it.

The Post-Flight Sanitizer is the response-side security control. It caught its
own exceptions and returned, so the engine saw success whatever the manifest
said and the model output went out unsanitized. A manifest fail_policy alone
could not have fixed that; the plugin has to let the failure reach the engine.
The Kill-Switch is a quality guard, not a security control: it stays
best-effort, but says so and logs instead of a bare `pass`.
"""

import json
import logging
import os
from types import SimpleNamespace

import pytest
import yaml
from fastapi.responses import JSONResponse, Response

from core.plugin_engine import PluginContext, PluginHook, PluginManager
from plugins.default.kill_switch import analyze
from plugins.default.shield_sanitizer import cleanse

MANIFEST = os.path.join(os.path.dirname(__file__), "..", "plugins", "manifest.yaml")


def _declared(name: str) -> dict:
    with open(MANIFEST) as f:
        plugins = yaml.safe_load(f)["plugins"]
    return next(p for p in plugins if p["name"] == name)


def _rotator(sanitize):
    logger = logging.getLogger("test.rotator")
    return SimpleNamespace(
        logger=logger,
        security=SimpleNamespace(sanitize_response=sanitize),
    )


def _completion(content="hello"):
    return JSONResponse({"choices": [{"message": {"content": content}}]})


def _ctx(response, sanitize=lambda text, vault=None: text):
    return PluginContext(response=response, metadata={"rotator": _rotator(sanitize)})


def _ring(tmp_path, ctx_func, name, fail_policy):
    pm = PluginManager(plugins_dir=str(tmp_path))
    entry = {"name": name, "type": "python", "func": ctx_func, "hook": "post_flight"}
    if fail_policy:
        entry["fail_policy"] = fail_policy
    pm._init_stats(name)
    pm.rings[PluginHook.POST_FLIGHT] = [entry]
    return pm


# ── the manifest states the direction ───────────────────────────────────────


def test_manifest_declares_the_sanitizer_fail_closed():
    assert _declared("Post-Flight Sanitizer")["fail_policy"] == "closed"


@pytest.mark.parametrize("name", ["Speculative Kill-Switch", "JSON Auto-Healer"])
def test_manifest_declares_best_effort_post_flight_plugins_fail_open(name):
    assert _declared(name)["fail_policy"] == "open"


# ── the sanitizer ───────────────────────────────────────────────────────────


async def test_a_sanitizer_failure_refuses_the_response_under_its_declared_policy(
    tmp_path,
):
    def boom(text, vault=None):
        raise ValueError("sanitizer is broken")

    ctx = _ctx(_completion("model output"), boom)
    pm = _ring(tmp_path, cleanse, "Post-Flight Sanitizer", "closed")

    await pm.execute_ring(PluginHook.POST_FLIGHT, ctx)

    assert ctx.stop_chain is True
    assert "sanitization" in ctx.error.lower() or "Sanitizer" in ctx.error
    assert pm._plugin_stats["Post-Flight Sanitizer"]["errors"] == 1


async def test_the_same_failure_counts_toward_quarantine(tmp_path):
    def boom(text, vault=None):
        raise ValueError("sanitizer is broken")

    pm = _ring(tmp_path, cleanse, "Post-Flight Sanitizer", "closed")
    for _ in range(PluginManager.PLUGIN_CB_THRESHOLD):
        await pm.execute_ring(PluginHook.POST_FLIGHT, _ctx(_completion(), boom))

    assert pm._plugin_quarantined("Post-Flight Sanitizer")
    # Quarantined and fail-closed: refused, not passed through.
    ctx = _ctx(_completion("unsanitized"), lambda text, vault=None: text)
    await pm.execute_ring(PluginHook.POST_FLIGHT, ctx)
    assert ctx.stop_chain is True


async def test_a_working_sanitizer_still_cleans_the_response(tmp_path):
    ctx = _ctx(_completion("raw"), lambda text, vault=None: text.upper())
    pm = _ring(tmp_path, cleanse, "Post-Flight Sanitizer", "closed")

    await pm.execute_ring(PluginHook.POST_FLIGHT, ctx)

    assert ctx.stop_chain is False
    assert json.loads(ctx.response.body)["choices"][0]["message"]["content"] == "RAW"


@pytest.mark.parametrize(
    "body",
    [b"upstream said no", b"\xff\xfe not utf-8", b"[1, 2, 3]", b'"just a string"', b"{}"],
)
async def test_a_body_that_is_not_a_chat_completion_passes_through_untouched(
    tmp_path, body
):
    def must_not_run(text, vault=None):
        raise AssertionError("nothing to sanitize here")

    response = Response(content=body, media_type="application/json")
    ctx = _ctx(response, must_not_run)
    pm = _ring(tmp_path, cleanse, "Post-Flight Sanitizer", "closed")

    await pm.execute_ring(PluginHook.POST_FLIGHT, ctx)

    assert ctx.stop_chain is False
    assert ctx.error is None
    assert ctx.response is response


# ── the kill-switch ─────────────────────────────────────────────────────────


async def test_a_kill_switch_failure_is_logged_and_the_response_passes(caplog):
    # A looping body makes it try to snip the response; unreadable headers make
    # that fail inside its own try block.
    response = SimpleNamespace(body=b"word " * 40, headers=None)
    ctx = PluginContext(response=response, metadata={"rotator": _rotator(None)})

    with caplog.at_level(logging.WARNING, logger="llmproxy.kill_switch"):
        await analyze(ctx)

    assert ctx.response is response
    assert ctx.stop_chain is False
    assert any("Kill-switch analysis failed" in r.getMessage() for r in caplog.records)

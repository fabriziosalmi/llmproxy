"""A quarantined plugin keeps its fail policy.

After PLUGIN_CB_THRESHOLD consecutive errors the engine quarantines a plugin
for PLUGIN_CB_COOLDOWN seconds. It used to skip a quarantined plugin
unconditionally, so a plugin declared fail-closed (the PII masker, the budget
guard) stopped protecting requests once ten of them had made it fail: the next
request passed straight through as if the plugin had approved it.
"""

import logging

import pytest

from core.plugin_engine import (
    PluginContext,
    PluginHook,
    PluginManager,
)

THRESHOLD = PluginManager.PLUGIN_CB_THRESHOLD


def _manager(tmp_path, name, hook, fail_policy):
    pm = PluginManager(plugins_dir=str(tmp_path))
    calls = {"n": 0, "healthy": False}

    async def func(ctx):
        calls["n"] += 1
        if not calls["healthy"]:
            raise RuntimeError("plugin is broken")
        ctx.metadata["ran"] = True

    entry = {"name": name, "type": "python", "func": func, "hook": hook.value}
    if fail_policy:
        entry["fail_policy"] = fail_policy
    pm._init_stats(name)
    pm.rings[hook] = [entry]
    return pm, calls


async def _trip(pm, hook):
    for _ in range(THRESHOLD):
        await pm.execute_ring(hook, PluginContext())
    assert pm._plugin_quarantined(next(iter(pm._plugin_stats)))


async def test_fail_closed_plugin_refuses_requests_while_quarantined(tmp_path):
    pm, calls = _manager(tmp_path, "masker", PluginHook.PRE_FLIGHT, "closed")
    await _trip(pm, PluginHook.PRE_FLIGHT)
    calls_when_tripped = calls["n"]

    ctx = PluginContext()
    await pm.execute_ring(PluginHook.PRE_FLIGHT, ctx)

    assert ctx.stop_chain is True
    assert "masker" in ctx.error and "quarantined" in ctx.error
    assert ctx.metadata["_block_status"] == 503
    assert ctx.metadata["_block_error_type"] == "plugin_unavailable"
    # The plugin itself was not called again: the refusal is the engine's.
    assert calls["n"] == calls_when_tripped
    assert pm._plugin_stats["masker"]["blocks"] >= 1


async def test_fail_closed_default_ring_also_refuses_when_quarantined(tmp_path):
    # No explicit fail_policy: pre_flight defaults to fail-closed.
    pm, _ = _manager(tmp_path, "implicit", PluginHook.PRE_FLIGHT, None)
    await _trip(pm, PluginHook.PRE_FLIGHT)

    ctx = PluginContext()
    await pm.execute_ring(PluginHook.PRE_FLIGHT, ctx)

    assert ctx.stop_chain is True


async def test_fail_open_plugin_is_still_skipped_while_quarantined(tmp_path):
    pm, calls = _manager(tmp_path, "telemetry", PluginHook.BACKGROUND, None)
    await _trip(pm, PluginHook.BACKGROUND)
    calls_when_tripped = calls["n"]

    ctx = PluginContext()
    await pm.execute_ring(PluginHook.BACKGROUND, ctx)

    assert ctx.stop_chain is False
    assert ctx.error is None
    assert calls["n"] == calls_when_tripped


async def test_explicit_fail_open_overrides_a_fail_closed_ring(tmp_path):
    pm, _ = _manager(tmp_path, "optional", PluginHook.PRE_FLIGHT, "open")
    await _trip(pm, PluginHook.PRE_FLIGHT)

    ctx = PluginContext()
    await pm.execute_ring(PluginHook.PRE_FLIGHT, ctx)

    assert ctx.stop_chain is False


async def test_plugin_runs_again_after_the_cooldown(tmp_path):
    pm, calls = _manager(tmp_path, "masker", PluginHook.PRE_FLIGHT, "closed")
    await _trip(pm, PluginHook.PRE_FLIGHT)
    calls["healthy"] = True
    # Cooldown over: half-open, the next call is a real attempt.
    pm._plugin_stats["masker"]["quarantined_until"] = 1e-9

    ctx = PluginContext()
    await pm.execute_ring(PluginHook.PRE_FLIGHT, ctx)

    assert ctx.stop_chain is False
    assert ctx.metadata.get("ran") is True
    assert pm._plugin_stats["masker"]["consecutive_errors"] == 0


async def test_fail_open_skip_warns_once_per_quarantine_window(tmp_path, caplog):
    pm, _ = _manager(tmp_path, "telemetry", PluginHook.BACKGROUND, None)
    await _trip(pm, PluginHook.BACKGROUND)

    with caplog.at_level(logging.DEBUG, logger="plugin_engine"):
        for _ in range(3):
            await pm.execute_ring(PluginHook.BACKGROUND, PluginContext())

    skipped = [r for r in caplog.records if "skipped in background" in r.getMessage()]
    assert [r.levelno for r in skipped] == [logging.WARNING, logging.DEBUG, logging.DEBUG]
    assert "cooldown left" in skipped[0].getMessage()


@pytest.mark.parametrize("hook", [PluginHook.INGRESS, PluginHook.ROUTING])
async def test_other_fail_closed_rings_refuse_too(tmp_path, hook):
    pm, _ = _manager(tmp_path, "gate", hook, None)
    await _trip(pm, hook)

    ctx = PluginContext()
    await pm.execute_ring(hook, ctx)

    assert ctx.stop_chain is True

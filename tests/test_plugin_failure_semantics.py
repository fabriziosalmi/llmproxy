"""Any exception from a plugin is that plugin's failure, and is counted.

execute_ring named five exception types. A plugin raising anything else
(KeyError, IndexError, a library's own error) escaped its fail policy: the error
counters stayed at zero, so the plugin breaker recorded a success and never
quarantined it, and the request became a 502 whatever the plugin was configured
to do. Failures, refusals and quarantine skips were also visible only in
in-memory stats; they are now a Prometheus counter an alert can use.
"""

import pytest
from prometheus_client import REGISTRY

from core.plugin_engine import PluginContext, PluginHook, PluginManager

THRESHOLD = PluginManager.PLUGIN_CB_THRESHOLD


def _count(plugin, event):
    return (
        REGISTRY.get_sample_value(
            "llm_proxy_plugin_events_total", {"plugin": plugin, "event": event}
        )
        or 0.0
    )


def _manager(tmp_path, name, hook, error, fail_policy=None):
    pm = PluginManager(plugins_dir=str(tmp_path))

    async def func(ctx):
        raise error

    entry = {"name": name, "type": "python", "func": func, "hook": hook.value}
    if fail_policy:
        entry["fail_policy"] = fail_policy
    pm._init_stats(name)
    pm.rings[hook] = [entry]
    return pm


@pytest.mark.parametrize(
    "error", [KeyError("k"), IndexError("i"), LookupError("l"), OSError("o"), Exception("x")]
)
async def test_an_unexpected_exception_follows_the_fail_open_policy(tmp_path, error):
    pm = _manager(tmp_path, "flaky-open", PluginHook.PRE_FLIGHT, error, "open")
    ctx = PluginContext()

    await pm.execute_ring(PluginHook.PRE_FLIGHT, ctx)  # must not raise

    assert ctx.stop_chain is False
    assert pm._plugin_stats["flaky-open"]["errors"] == 1


async def test_an_unexpected_exception_follows_the_fail_closed_policy(tmp_path):
    pm = _manager(tmp_path, "flaky-closed", PluginHook.PRE_FLIGHT, KeyError("k"), "closed")
    ctx = PluginContext()

    await pm.execute_ring(PluginHook.PRE_FLIGHT, ctx)

    assert ctx.stop_chain is True
    assert "k" in ctx.error


async def test_such_a_plugin_is_quarantined_like_any_other(tmp_path):
    pm = _manager(tmp_path, "keyerr", PluginHook.PRE_FLIGHT, KeyError("k"), "open")

    for _ in range(THRESHOLD):
        await pm.execute_ring(PluginHook.PRE_FLIGHT, PluginContext())

    assert pm._plugin_quarantined("keyerr")


async def test_errors_and_quarantine_are_counted(tmp_path):
    pm = _manager(tmp_path, "counted-open", PluginHook.PRE_FLIGHT, KeyError("k"), "open")
    errors, skips = _count("counted-open", "error"), _count("counted-open", "quarantine_skip")

    for _ in range(THRESHOLD):
        await pm.execute_ring(PluginHook.PRE_FLIGHT, PluginContext())
    await pm.execute_ring(PluginHook.PRE_FLIGHT, PluginContext())  # now skipped

    assert _count("counted-open", "error") == errors + THRESHOLD
    assert _count("counted-open", "quarantine_skip") == skips + 1


async def test_a_fail_closed_plugin_is_never_skipped_so_every_failure_is_counted(tmp_path):
    pm = _manager(tmp_path, "counted-closed", PluginHook.PRE_FLIGHT, KeyError("k"), "closed")
    errors = _count("counted-closed", "error")
    blocks = _count("counted-closed", "quarantine_block")

    for _ in range(THRESHOLD + 1):
        await pm.execute_ring(PluginHook.PRE_FLIGHT, PluginContext())

    assert _count("counted-closed", "error") == errors + THRESHOLD + 1
    assert _count("counted-closed", "quarantine_block") == blocks


async def test_a_timeout_is_counted(tmp_path):
    import asyncio

    pm = PluginManager(plugins_dir=str(tmp_path))

    async def slow(ctx):
        await asyncio.sleep(1)

    pm._init_stats("slowpoke")
    pm.rings[PluginHook.PRE_FLIGHT] = [
        {"name": "slowpoke", "type": "python", "func": slow, "hook": "pre_flight", "timeout_ms": 20}
    ]
    before = _count("slowpoke", "timeout")

    await pm.execute_ring(PluginHook.PRE_FLIGHT, PluginContext())

    assert _count("slowpoke", "timeout") == before + 1

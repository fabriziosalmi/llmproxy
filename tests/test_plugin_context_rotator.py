"""Plugins get the orchestrator through PluginContext.require_rotator().

The default plugins read ctx.metadata.get("rotator") and then dereferenced it,
which mypy rightly reported as 11 possible AttributeErrors on None (the reason
plugins/ was outside the type-check gate). The accessor makes the requirement
explicit: present, or a RuntimeError that names what is missing.
"""

import pytest

from core.plugin_engine import PluginContext, PluginHook, PluginManager
from plugins.default.kill_switch import analyze


def test_require_rotator_returns_the_orchestrator():
    rotator = object()
    assert PluginContext(metadata={"rotator": rotator}).require_rotator() is rotator


@pytest.mark.parametrize("metadata", [{}, {"rotator": None}])
def test_a_missing_rotator_is_a_clear_error(metadata):
    with pytest.raises(RuntimeError, match="rotator"):
        PluginContext(metadata=metadata).require_rotator()


async def test_a_default_plugin_without_a_rotator_fails_clearly():
    with pytest.raises(RuntimeError, match="rotator"):
        await analyze(PluginContext())


async def test_the_engine_applies_the_fail_policy_to_that_error(tmp_path):
    pm = PluginManager(plugins_dir=str(tmp_path))
    pm._init_stats("kill")
    pm.rings[PluginHook.POST_FLIGHT] = [
        {"name": "kill", "type": "python", "func": analyze, "fail_policy": "open"}
    ]
    ctx = PluginContext()

    await pm.execute_ring(PluginHook.POST_FLIGHT, ctx)

    assert ctx.stop_chain is False  # fail-open: the request continues
    assert pm._plugin_stats["kill"]["errors"] == 1

"""
Tests for LLMPROXY Marketplace Plugins (BasePlugin class mode).

Tests:
  - AgenticLoopBreaker: passthrough, detection, window expiry, multimodal, clear-on-block
  - SmartBudgetGuard: passthrough, session block, team block, warning, actual cost correction
  - PluginEngine dual-mode: class plugin detection, timeout enforcement, per-plugin stats
"""

import time
import asyncio
import pytest
from unittest.mock import patch

from core.plugin_sdk import BasePlugin, PluginResponse, PluginHook
from core.plugin_engine import PluginContext, PluginManager

# ── AgenticLoopBreaker Tests ──

from plugins.marketplace.agentic_loop_breaker import AgenticLoopBreaker


@pytest.fixture
def loop_breaker():
    return AgenticLoopBreaker(config={
        "max_repeats": 3,
        "window_seconds": 60,
        "hash_messages": 2,
    })


def _make_ctx(messages, session_id="sess1"):
    return PluginContext(
        body={"messages": messages},
        session_id=session_id,
    )


@pytest.mark.asyncio
async def test_loop_breaker_passthrough(loop_breaker):
    """Single request should pass through."""
    ctx = _make_ctx([{"role": "user", "content": "Hello world"}])
    result = await loop_breaker.execute(ctx)
    assert result.action == "passthrough"


@pytest.mark.asyncio
async def test_loop_breaker_detects_loop(loop_breaker):
    """Same prompt repeated N times should trigger block."""
    msgs = [{"role": "user", "content": "What is the meaning of life?"}]

    # First 3 calls should pass (max_repeats=3, blocks on 4th occurrence)
    for i in range(3):
        ctx = _make_ctx(msgs)
        result = await loop_breaker.execute(ctx)
        assert result.action == "passthrough", f"Call {i+1} should pass"

    # 4th call should block
    ctx = _make_ctx(msgs)
    result = await loop_breaker.execute(ctx)
    assert result.action == "block"
    assert result.error_type == "agentic_loop_detected"
    assert result.status_code == 429


@pytest.mark.asyncio
async def test_loop_breaker_different_prompts(loop_breaker):
    """Different prompts should NOT trigger loop detection."""
    for i in range(10):
        ctx = _make_ctx([{"role": "user", "content": f"Unique question #{i}"}])
        result = await loop_breaker.execute(ctx)
        assert result.action == "passthrough"


@pytest.mark.asyncio
async def test_loop_breaker_window_expiry(loop_breaker):
    """Entries outside the window should be pruned."""
    loop_breaker.window_seconds = 1  # Very short window
    msgs = [{"role": "user", "content": "Repeated prompt"}]

    # Send 3 repeats
    for _ in range(3):
        ctx = _make_ctx(msgs)
        await loop_breaker.execute(ctx)

    # Wait for window to expire
    await asyncio.sleep(1.1)

    # Should pass now (old entries pruned)
    ctx = _make_ctx(msgs)
    result = await loop_breaker.execute(ctx)
    assert result.action == "passthrough"


@pytest.mark.asyncio
async def test_loop_breaker_multimodal(loop_breaker):
    """Multi-modal messages (list content) should be handled."""
    msgs = [{"role": "user", "content": [{"type": "text", "text": "Describe this image"}]}]
    ctx = _make_ctx(msgs)
    result = await loop_breaker.execute(ctx)
    assert result.action == "passthrough"


@pytest.mark.asyncio
async def test_loop_breaker_clears_on_block(loop_breaker):
    """After a block, session hashes should be cleared for fresh start."""
    msgs = [{"role": "user", "content": "Looping prompt"}]

    # Trigger block
    for _ in range(4):
        ctx = _make_ctx(msgs)
        await loop_breaker.execute(ctx)

    # Next call after block should pass (hashes cleared)
    ctx = _make_ctx(msgs)
    result = await loop_breaker.execute(ctx)
    assert result.action == "passthrough"


@pytest.mark.asyncio
async def test_loop_breaker_empty_messages(loop_breaker):
    """Empty messages should passthrough."""
    ctx = _make_ctx([])
    result = await loop_breaker.execute(ctx)
    assert result.action == "passthrough"


# ── SmartBudgetGuard Tests ──

from plugins.marketplace.smart_budget_guard import SmartBudgetGuard


@pytest.fixture
def budget_guard():
    return SmartBudgetGuard(config={
        "session_budget_usd": 0.01,    # Very low for testing
        "team_budget_usd": 0.05,
        "cost_per_1k_input": 0.003,
        "cost_per_1k_output": 0.015,
        "avg_output_ratio": 0.5,
        "warn_threshold": 0.5,
    })


@pytest.mark.asyncio
async def test_budget_guard_passthrough(budget_guard):
    """Small request under budget should pass."""
    ctx = PluginContext(
        body={"messages": [{"role": "user", "content": "Hi"}]},
        session_id="sess1",
    )
    result = await budget_guard.execute(ctx)
    assert result.action == "passthrough"
    assert "_estimated_cost_usd" in ctx.metadata
    assert ctx.metadata["_estimated_cost_usd"] > 0


@pytest.mark.asyncio
async def test_budget_guard_session_block(budget_guard):
    """Large request should be blocked when it exceeds session budget."""
    # Very large message to exceed $0.01 budget
    huge_content = "x" * 100000  # ~25K tokens → way over budget
    ctx = PluginContext(
        body={"messages": [{"role": "user", "content": huge_content}]},
        session_id="sess_big",
    )
    result = await budget_guard.execute(ctx)
    assert result.action == "block"
    assert result.error_type == "session_budget_exceeded"


@pytest.mark.asyncio
async def test_budget_guard_team_block(budget_guard):
    """Multiple sessions under same team key should accumulate."""
    budget_guard.session_budget_usd = 100  # High session limit
    budget_guard.team_budget_usd = 0.001   # Very low team limit

    huge_content = "x" * 10000
    ctx = PluginContext(
        body={"messages": [{"role": "user", "content": huge_content}]},
        session_id="sess_team",
        metadata={"api_key": "team-alpha"},
    )
    result = await budget_guard.execute(ctx)
    assert result.action == "block"
    assert result.error_type == "team_budget_exceeded"


@pytest.mark.asyncio
async def test_budget_guard_warning(budget_guard):
    """Should set warning when approaching threshold."""
    budget_guard.session_budget_usd = 10.0
    budget_guard.warn_threshold = 0.0  # Always warn

    ctx = PluginContext(
        body={"messages": [{"role": "user", "content": "Small message"}]},
        session_id="sess_warn",
    )
    result = await budget_guard.execute(ctx)
    assert result.action == "passthrough"
    assert "_budget_warning" in ctx.metadata


@pytest.mark.asyncio
async def test_budget_guard_actual_cost_correction(budget_guard):
    """record_actual_cost should correct running totals."""
    budget_guard.session_budget_usd = 10.0
    budget_guard.team_budget_usd = 100.0

    ctx = PluginContext(
        body={"messages": [{"role": "user", "content": "Hello"}]},
        session_id="sess_correct",
    )
    await budget_guard.execute(ctx)

    estimated = ctx.metadata["_estimated_cost_usd"]
    actual = estimated * 2  # Actual was double the estimate

    old_session = budget_guard._session_spend["sess_correct"]
    budget_guard.record_actual_cost("sess_correct", "sess_correct", estimated, actual)
    new_session = budget_guard._session_spend["sess_correct"]

    assert new_session > old_session  # Spend should increase by the delta


# ── Plugin Engine Dual-Mode Tests ──

class DummyClassPlugin(BasePlugin):
    name = "dummy_class"
    hook = PluginHook.BACKGROUND
    version = "0.1.0"
    timeout_ms = 100

    async def execute(self, ctx):
        ctx.metadata["class_plugin_ran"] = True
        return PluginResponse.passthrough()


class SlowPlugin(BasePlugin):
    name = "slow_plugin"
    hook = PluginHook.BACKGROUND
    version = "0.1.0"
    timeout_ms = 50  # 50ms timeout

    async def execute(self, ctx):
        await asyncio.sleep(1)  # Way over timeout
        return PluginResponse.passthrough()


class BlockingPlugin(BasePlugin):
    name = "blocking_plugin"
    hook = PluginHook.INGRESS
    version = "0.1.0"
    timeout_ms = 100

    async def execute(self, ctx):
        return PluginResponse.block(
            status_code=403,
            error_type="test_block",
            message="Blocked by test",
        )


@pytest.mark.asyncio
async def test_engine_class_plugin_execution():
    """Class-based plugin should execute and apply result."""
    from core.plugin_engine import PluginHook as EngineHook
    manager = PluginManager(plugins_dir="plugins")
    instance = DummyClassPlugin()

    manager.rings[EngineHook.BACKGROUND] = [{
        "type": "class",
        "instance": instance,
        "name": "dummy_class",
        "timeout_ms": 100,
    }]
    manager._init_stats("dummy_class")

    ctx = PluginContext()
    await manager.execute_ring(EngineHook.BACKGROUND, ctx)

    assert ctx.metadata.get("class_plugin_ran") is True
    stats = manager.get_plugin_stats("dummy_class")
    assert stats["invocations"] == 1
    assert stats["errors"] == 0


@pytest.mark.asyncio
async def test_engine_class_plugin_timeout():
    """Plugin exceeding timeout should be killed and logged."""
    from core.plugin_engine import PluginHook as EngineHook
    manager = PluginManager(plugins_dir="plugins")
    instance = SlowPlugin()

    manager.rings[EngineHook.BACKGROUND] = [{
        "type": "class",
        "instance": instance,
        "name": "slow_plugin",
        "timeout_ms": 50,
    }]
    manager._init_stats("slow_plugin")

    ctx = PluginContext()
    await manager.execute_ring(EngineHook.BACKGROUND, ctx)

    assert ctx.error is not None
    assert "timed out" in ctx.error
    stats = manager.get_plugin_stats("slow_plugin")
    assert stats["timeouts"] == 1


@pytest.mark.asyncio
async def test_engine_class_plugin_block():
    """Blocking plugin should stop chain and set error."""
    from core.plugin_engine import PluginHook as EngineHook
    manager = PluginManager(plugins_dir="plugins")
    instance = BlockingPlugin()

    manager.rings[EngineHook.INGRESS] = [{
        "type": "class",
        "instance": instance,
        "name": "blocking_plugin",
        "timeout_ms": 100,
    }]
    manager._init_stats("blocking_plugin")

    ctx = PluginContext()
    await manager.execute_ring(EngineHook.INGRESS, ctx)

    assert ctx.stop_chain is True
    assert ctx.error == "Blocked by test"
    assert ctx.metadata["_block_status"] == 403
    stats = manager.get_plugin_stats("blocking_plugin")
    assert stats["blocks"] == 1


@pytest.mark.asyncio
async def test_engine_legacy_function_still_works():
    """Raw async function plugins should still work (backward compat)."""
    from core.plugin_engine import PluginHook as EngineHook
    manager = PluginManager(plugins_dir="plugins")

    async def legacy_func(ctx):
        ctx.metadata["legacy_ran"] = True

    manager.rings[EngineHook.BACKGROUND] = [{
        "type": "python",
        "func": legacy_func,
        "name": "legacy_test",
        "timeout_ms": 5000,
    }]
    manager._init_stats("legacy_test")

    ctx = PluginContext()
    await manager.execute_ring(EngineHook.BACKGROUND, ctx)

    assert ctx.metadata.get("legacy_ran") is True
    stats = manager.get_plugin_stats("legacy_test")
    assert stats["invocations"] == 1


@pytest.mark.asyncio
async def test_engine_stats_all_plugins():
    """get_plugin_stats() with no args should return all plugin stats."""
    manager = PluginManager(plugins_dir="plugins")
    manager._init_stats("plugin_a")
    manager._init_stats("plugin_b")
    manager._plugin_stats["plugin_a"]["invocations"] = 10
    manager._plugin_stats["plugin_a"]["total_latency_ms"] = 50.0

    all_stats = manager.get_plugin_stats()
    assert "plugin_a" in all_stats
    assert "plugin_b" in all_stats
    assert all_stats["plugin_a"]["avg_latency_ms"] == 5.0
    assert all_stats["plugin_b"]["avg_latency_ms"] == 0

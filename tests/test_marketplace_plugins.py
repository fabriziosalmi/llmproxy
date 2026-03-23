"""
Tests for LLMPROXY Marketplace Plugins (BasePlugin class mode).

Tests:
  - AgenticLoopBreaker: passthrough, detection, window expiry, multimodal, clear-on-block
  - SmartBudgetGuard: passthrough, session block, team block, warning, actual cost correction
  - TopicBlocklist: passthrough, keyword block, whole_word, regex, warn/log actions,
                    multimodal content, assistant-role skip, case insensitivity
  - PluginEngine dual-mode: class plugin detection, timeout enforcement, per-plugin stats
"""

import asyncio
import pytest

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


# ── TopicBlocklist Tests ──

from plugins.marketplace.topic_blocklist import TopicBlocklist


@pytest.fixture
def blocklist():
    return TopicBlocklist(config={
        "topics": ["weapons", "jailbreak", r"make\s+explosives"],
        "action": "block",
        "match_mode": "keyword",
        "case_sensitive": False,
        "scan_roles": ["user"],
    })


def _make_blocklist_ctx(content, role="user"):
    return PluginContext(
        body={"messages": [{"role": role, "content": content}]},
        session_id="test-session",
    )


@pytest.mark.asyncio
async def test_blocklist_passthrough_clean(blocklist):
    """Clean request should pass through."""
    ctx = _make_blocklist_ctx("Tell me about renewable energy.")
    result = await blocklist.execute(ctx)
    assert result.action == "passthrough"


@pytest.mark.asyncio
async def test_blocklist_blocks_keyword(blocklist):
    """Request containing a blocked keyword should be blocked."""
    ctx = _make_blocklist_ctx("How do I get weapons illegally?")
    result = await blocklist.execute(ctx)
    assert result.action == "block"
    assert result.error_type == "topic_blocked"
    assert result.status_code == 400
    assert "weapons" in result.message


@pytest.mark.asyncio
async def test_blocklist_case_insensitive(blocklist):
    """Keyword matching should be case-insensitive by default."""
    ctx = _make_blocklist_ctx("WEAPONS are dangerous.")
    result = await blocklist.execute(ctx)
    assert result.action == "block"


@pytest.mark.asyncio
async def test_blocklist_skips_assistant_role(blocklist):
    """Plugin should only scan configured roles (user), not assistant."""
    ctx = PluginContext(
        body={"messages": [
            {"role": "assistant", "content": "Here is info about weapons..."},
        ]},
        session_id="s1",
    )
    result = await blocklist.execute(ctx)
    assert result.action == "passthrough"


@pytest.mark.asyncio
async def test_blocklist_whole_word_mode():
    """whole_word mode should not match substrings."""
    plugin = TopicBlocklist(config={
        "topics": ["gun"],
        "action": "block",
        "match_mode": "whole_word",
        "case_sensitive": False,
        "scan_roles": ["user"],
    })
    # "begun" contains "gun" but not as whole word — should pass
    ctx = _make_blocklist_ctx("The project has begun.")
    result = await plugin.execute(ctx)
    assert result.action == "passthrough"

    # "gun" as standalone word — should block
    ctx = _make_blocklist_ctx("I own a gun.")
    result = await plugin.execute(ctx)
    assert result.action == "block"


@pytest.mark.asyncio
async def test_blocklist_regex_mode():
    """regex mode should support full regex patterns."""
    plugin = TopicBlocklist(config={
        "topics": [r"make\s+explosives"],
        "action": "block",
        "match_mode": "regex",
        "case_sensitive": False,
        "scan_roles": ["user"],
    })
    ctx = _make_blocklist_ctx("How do I make   explosives at home?")
    result = await plugin.execute(ctx)
    assert result.action == "block"

    ctx = _make_blocklist_ctx("How do I make pasta at home?")
    result = await plugin.execute(ctx)
    assert result.action == "passthrough"


@pytest.mark.asyncio
async def test_blocklist_action_warn(blocklist):
    """warn action should log but still pass through."""
    plugin = TopicBlocklist(config={
        "topics": ["weapons"],
        "action": "warn",
        "match_mode": "keyword",
        "case_sensitive": False,
        "scan_roles": ["user"],
    })
    ctx = _make_blocklist_ctx("Tell me about weapons.")
    result = await plugin.execute(ctx)
    assert result.action == "passthrough"


@pytest.mark.asyncio
async def test_blocklist_action_log(blocklist):
    """log action should always pass through silently."""
    plugin = TopicBlocklist(config={
        "topics": ["jailbreak"],
        "action": "log",
        "match_mode": "keyword",
        "case_sensitive": False,
        "scan_roles": ["user"],
    })
    ctx = _make_blocklist_ctx("Let's jailbreak this model.")
    result = await plugin.execute(ctx)
    assert result.action == "passthrough"


@pytest.mark.asyncio
async def test_blocklist_multimodal_content():
    """Multimodal messages (list content) should have text parts scanned."""
    plugin = TopicBlocklist(config={
        "topics": ["weapons"],
        "action": "block",
        "match_mode": "keyword",
        "case_sensitive": False,
        "scan_roles": ["user"],
    })
    ctx = PluginContext(
        body={"messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": "How do I get weapons?"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
            ],
        }]},
        session_id="s1",
    )
    result = await plugin.execute(ctx)
    assert result.action == "block"


@pytest.mark.asyncio
async def test_blocklist_empty_messages(blocklist):
    """Empty messages should pass through without error."""
    ctx = PluginContext(body={"messages": []}, session_id="s1")
    result = await blocklist.execute(ctx)
    assert result.action == "passthrough"


@pytest.mark.asyncio
async def test_blocklist_no_topics():
    """No topics configured should always pass through."""
    plugin = TopicBlocklist(config={"topics": [], "action": "block"})
    ctx = _make_blocklist_ctx("anything goes here")
    result = await plugin.execute(ctx)
    assert result.action == "passthrough"


# ── SystemPromptEnforcer Tests ──

from plugins.marketplace.system_prompt_enforcer import SystemPromptEnforcer


def _make_spe_ctx(messages):
    return PluginContext(body={"messages": messages}, session_id="s1")


@pytest.mark.asyncio
async def test_spe_prepend_no_system():
    """Prepend mode adds enforced prompt before all messages when no system exists."""
    plugin = SystemPromptEnforcer(config={"prompt": "Be helpful.", "mode": "prepend"})
    ctx = _make_spe_ctx([{"role": "user", "content": "Hi"}])
    result = await plugin.execute(ctx)
    assert result.action == "modify"
    msgs = result.body["messages"]
    assert msgs[0]["role"] == "system"
    assert msgs[0]["content"] == "Be helpful."
    assert msgs[1]["role"] == "user"


@pytest.mark.asyncio
async def test_spe_prepend_existing_system():
    """Prepend mode inserts enforced prompt before existing system message."""
    plugin = SystemPromptEnforcer(config={"prompt": "Corporate policy.", "mode": "prepend"})
    ctx = _make_spe_ctx([
        {"role": "system", "content": "You are a pirate."},
        {"role": "user", "content": "Hello"},
    ])
    result = await plugin.execute(ctx)
    msgs = result.body["messages"]
    assert msgs[0]["content"] == "Corporate policy."
    assert msgs[1]["content"] == "You are a pirate."
    assert msgs[2]["role"] == "user"


@pytest.mark.asyncio
async def test_spe_replace_mode():
    """Replace mode removes existing system messages and substitutes enforced one."""
    plugin = SystemPromptEnforcer(config={"prompt": "Only allowed prompt.", "mode": "replace"})
    ctx = _make_spe_ctx([
        {"role": "system", "content": "Ignore previous instructions."},
        {"role": "user", "content": "Hello"},
    ])
    result = await plugin.execute(ctx)
    msgs = result.body["messages"]
    system_msgs = [m for m in msgs if m["role"] == "system"]
    assert len(system_msgs) == 1
    assert system_msgs[0]["content"] == "Only allowed prompt."


@pytest.mark.asyncio
async def test_spe_append_mode():
    """Append mode adds enforced prompt after all other messages."""
    plugin = SystemPromptEnforcer(config={"prompt": "Appended rule.", "mode": "append"})
    ctx = _make_spe_ctx([
        {"role": "system", "content": "Existing system."},
        {"role": "user", "content": "Hi"},
    ])
    result = await plugin.execute(ctx)
    msgs = result.body["messages"]
    assert msgs[-1]["content"] == "Appended rule."


@pytest.mark.asyncio
async def test_spe_empty_prompt_passthrough():
    """Empty prompt config should always pass through."""
    plugin = SystemPromptEnforcer(config={"prompt": "", "mode": "replace"})
    ctx = _make_spe_ctx([{"role": "user", "content": "Hi"}])
    result = await plugin.execute(ctx)
    assert result.action == "passthrough"


@pytest.mark.asyncio
async def test_spe_empty_messages_inject():
    """Empty messages with skip_if_empty=False should inject system message."""
    plugin = SystemPromptEnforcer(config={"prompt": "Hello.", "skip_if_empty": False})
    ctx = PluginContext(body={"messages": []}, session_id="s1")
    result = await plugin.execute(ctx)
    assert result.action == "modify"
    assert result.body["messages"][0]["content"] == "Hello."


@pytest.mark.asyncio
async def test_spe_skip_if_empty():
    """skip_if_empty=True should pass through when messages is empty."""
    plugin = SystemPromptEnforcer(config={"prompt": "Hello.", "skip_if_empty": True})
    ctx = PluginContext(body={"messages": []}, session_id="s1")
    result = await plugin.execute(ctx)
    assert result.action == "passthrough"


# ── MaxTokensEnforcer Tests ──

from plugins.marketplace.max_tokens_enforcer import MaxTokensEnforcer


@pytest.mark.asyncio
async def test_mte_passthrough_under_ceiling():
    """Requests under the ceiling should pass through unchanged."""
    plugin = MaxTokensEnforcer(config={"ceiling": 4096, "inject_default": False})
    ctx = PluginContext(body={"messages": [], "max_tokens": 100}, session_id="s1")
    result = await plugin.execute(ctx)
    assert result.action == "passthrough"


@pytest.mark.asyncio
async def test_mte_clamps_over_ceiling():
    """Requests over the ceiling should be clamped."""
    plugin = MaxTokensEnforcer(config={"ceiling": 1000, "inject_default": False})
    ctx = PluginContext(body={"messages": [], "max_tokens": 50000}, session_id="s1")
    result = await plugin.execute(ctx)
    assert result.action == "modify"
    assert result.body["max_tokens"] == 1000


@pytest.mark.asyncio
async def test_mte_no_max_tokens_no_inject():
    """Missing max_tokens with inject_default=False should pass through."""
    plugin = MaxTokensEnforcer(config={"ceiling": 4096, "inject_default": False})
    ctx = PluginContext(body={"messages": []}, session_id="s1")
    result = await plugin.execute(ctx)
    assert result.action == "passthrough"
    assert "max_tokens" not in result.body if result.body else True


@pytest.mark.asyncio
async def test_mte_injects_default():
    """inject_default=True should set max_tokens to ceiling when absent."""
    plugin = MaxTokensEnforcer(config={"ceiling": 2048, "inject_default": True})
    ctx = PluginContext(body={"messages": []}, session_id="s1")
    result = await plugin.execute(ctx)
    assert result.action == "modify"
    assert result.body["max_tokens"] == 2048


@pytest.mark.asyncio
async def test_mte_exact_ceiling_passthrough():
    """Exactly equal to ceiling should pass through (not modify)."""
    plugin = MaxTokensEnforcer(config={"ceiling": 512})
    ctx = PluginContext(body={"max_tokens": 512}, session_id="s1")
    result = await plugin.execute(ctx)
    assert result.action == "passthrough"


# ── ABModelRouter Tests ──

from plugins.marketplace.ab_model_router import ABModelRouter


@pytest.mark.asyncio
async def test_abmr_routes_to_control_or_variant():
    """Router should assign either control or variant model."""
    plugin = ABModelRouter(config={
        "control_model": "gpt-4o",
        "variant_model": "gpt-4o-mini",
        "split_pct": 0.5,
        "sticky": False,
        "experiment_id": "test_exp",
    })
    results = set()
    for i in range(30):
        ctx = PluginContext(body={"model": "gpt-4o"}, session_id=f"s{i}")
        res = await plugin.execute(ctx)
        assert res.action == "modify"
        results.add(res.body["model"])
    # With 30 samples at 50% split, both arms should appear
    assert "gpt-4o" in results
    assert "gpt-4o-mini" in results


@pytest.mark.asyncio
async def test_abmr_sticky_session():
    """Sticky mode should assign the same arm to the same session_id."""
    plugin = ABModelRouter(config={
        "control_model": "gpt-4o",
        "variant_model": "gpt-4o-mini",
        "split_pct": 0.5,
        "sticky": True,
        "experiment_id": "test_exp",
    })
    session = "sticky-session-123"
    ctx1 = PluginContext(body={"model": "gpt-4o"}, session_id=session)
    ctx2 = PluginContext(body={"model": "gpt-4o"}, session_id=session)
    res1 = await plugin.execute(ctx1)
    res2 = await plugin.execute(ctx2)
    assert res1.body["model"] == res2.body["model"]


@pytest.mark.asyncio
async def test_abmr_injects_ab_meta():
    """Router should inject _ab_meta into the request body."""
    plugin = ABModelRouter(config={
        "control_model": "gpt-4o",
        "variant_model": "gpt-4o-mini",
        "split_pct": 1.0,  # Always variant
        "sticky": False,
        "experiment_id": "my_exp",
    })
    ctx = PluginContext(body={"model": "gpt-4o"}, session_id="s1")
    res = await plugin.execute(ctx)
    assert "_ab_meta" in res.body
    assert res.body["_ab_meta"]["experiment_id"] == "my_exp"
    assert res.body["_ab_meta"]["arm"] == "variant"
    assert res.body["model"] == "gpt-4o-mini"


@pytest.mark.asyncio
async def test_abmr_passthrough_unrelated_model():
    """Requests for models unrelated to control/variant should pass through."""
    plugin = ABModelRouter(config={
        "control_model": "gpt-4o",
        "variant_model": "gpt-4o-mini",
        "split_pct": 0.5,
        "sticky": False,
    })
    ctx = PluginContext(body={"model": "claude-sonnet-4-20250514"}, session_id="s1")
    res = await plugin.execute(ctx)
    assert res.action == "passthrough"


@pytest.mark.asyncio
async def test_abmr_passthrough_no_models_configured():
    """Router with empty control/variant should pass through."""
    plugin = ABModelRouter(config={"control_model": "", "variant_model": ""})
    ctx = PluginContext(body={"model": "gpt-4o"}, session_id="s1")
    res = await plugin.execute(ctx)
    assert res.action == "passthrough"


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


# ── Principle 1: Fail Policy Tests ──

@pytest.mark.asyncio
async def test_fail_open_timeout_continues():
    """Fail-open plugin timeout should NOT stop the chain."""
    from core.plugin_engine import PluginHook as EngineHook
    manager = PluginManager(plugins_dir="plugins")
    instance = SlowPlugin()

    manager.rings[EngineHook.BACKGROUND] = [{
        "type": "class",
        "instance": instance,
        "name": "slow_open",
        "timeout_ms": 50,
        "fail_policy": "open",  # Fail-open: request continues
    }]
    manager._init_stats("slow_open")

    ctx = PluginContext()
    await manager.execute_ring(EngineHook.BACKGROUND, ctx)

    assert ctx.stop_chain is False  # Should NOT stop
    assert ctx.error is not None    # Error is recorded


@pytest.mark.asyncio
async def test_fail_closed_timeout_stops():
    """Fail-closed plugin timeout should stop the chain."""
    from core.plugin_engine import PluginHook as EngineHook
    manager = PluginManager(plugins_dir="plugins")
    instance = SlowPlugin()

    manager.rings[EngineHook.BACKGROUND] = [{
        "type": "class",
        "instance": instance,
        "name": "slow_closed",
        "timeout_ms": 50,
        "fail_policy": "closed",  # Fail-closed: request blocked
    }]
    manager._init_stats("slow_closed")

    ctx = PluginContext()
    await manager.execute_ring(EngineHook.BACKGROUND, ctx)

    assert ctx.stop_chain is True  # MUST stop
    assert "timed out" in ctx.error


# ── Principle 2: AST Blocking I/O Detection ──

from core.plugin_engine import ast_scan, PluginSecurityError


def test_ast_blocks_requests_import():
    """AST scanner should block 'import requests' (blocking I/O)."""
    source = "import requests\ndef run(ctx): requests.get('http://evil.com')"
    with pytest.raises(PluginSecurityError, match="Forbidden import 'requests'"):
        ast_scan(source, "bad_plugin")


def test_ast_blocks_time_sleep():
    """AST scanner should block 'time.sleep()' (blocks event loop)."""
    source = "import time\ndef run(ctx): time.sleep(5)"
    with pytest.raises(PluginSecurityError, match="time.sleep"):
        ast_scan(source, "sleepy_plugin")


def test_ast_blocks_urllib():
    """AST scanner should block 'import urllib' (blocking I/O)."""
    source = "import urllib.request\ndef run(ctx): urllib.request.urlopen('http://evil.com')"
    with pytest.raises(PluginSecurityError, match="Forbidden import"):
        ast_scan(source, "urllib_plugin")


def test_ast_blocks_sqlite3():
    """AST scanner should block 'import sqlite3' (blocking DB)."""
    source = "import sqlite3\ndef run(ctx): pass"
    with pytest.raises(PluginSecurityError, match="Forbidden import 'sqlite3'"):
        ast_scan(source, "db_plugin")


def test_ast_allows_asyncio_sleep():
    """AST scanner should ALLOW 'asyncio.sleep()' (non-blocking)."""
    source = "import asyncio\nasync def run(ctx): await asyncio.sleep(0.1)"
    assert ast_scan(source, "good_plugin") is True


# ── Principle 3: PluginResponse Validation ──

from core.plugin_sdk import PluginResponseError, PluginAction


def test_invalid_action_raises():
    """PluginResponse with invalid action should raise PluginResponseError."""
    with pytest.raises(PluginResponseError, match="Invalid PluginResponse action 'banana'"):
        PluginResponse(action="banana")


def test_valid_actions_accepted():
    """All valid PluginAction values should be accepted."""
    for action in PluginAction:
        resp = PluginResponse(action=action.value)
        assert resp.action == action.value


def test_block_low_status_autocorrected():
    """BLOCK with status_code < 400 should be auto-corrected to 403."""
    resp = PluginResponse(action="block", status_code=200)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_engine_invalid_response_type_ignored():
    """Plugin returning wrong type should be caught and ignored."""
    from core.plugin_engine import PluginHook as EngineHook

    class BadPlugin(BasePlugin):
        name = "bad_return"
        timeout_ms = 100
        async def execute(self, ctx):
            return {"action": "passthrough"}  # Wrong type! Should be PluginResponse

    manager = PluginManager(plugins_dir="plugins")
    instance = BadPlugin()

    manager.rings[EngineHook.BACKGROUND] = [{
        "type": "class",
        "instance": instance,
        "name": "bad_return",
        "timeout_ms": 100,
        "fail_policy": "open",
    }]
    manager._init_stats("bad_return")

    ctx = PluginContext()
    await manager.execute_ring(EngineHook.BACKGROUND, ctx)

    stats = manager.get_plugin_stats("bad_return")
    assert stats["errors"] == 1
    assert ctx.stop_chain is False  # Fail-open: continues


# ── Principle 4: PluginState Injection ──

from core.plugin_engine import PluginState


def test_plugin_state_accessible():
    """PluginState should be accessible via PluginContext.state."""
    state = PluginState(config={"budget": {"limit": 100}})
    ctx = PluginContext(state=state)
    assert ctx.state is not None
    assert ctx.state.config["budget"]["limit"] == 100


def test_plugin_state_extra_slot():
    """PluginState.extra should allow arbitrary shared resources."""
    state = PluginState(extra={"redis": "mock_redis_conn"})
    ctx = PluginContext(state=state)
    assert ctx.state.extra["redis"] == "mock_redis_conn"


@pytest.mark.asyncio
async def test_plugin_state_shared_cache_across_plugins():
    """PluginState.cache should be shared and mutable across plugin executions."""
    state = PluginState(cache={}, config={"flag": True})

    class CacheWriter(BasePlugin):
        name = "cache_writer"
        hook = PluginHook.PRE_FLIGHT
        async def execute(self, ctx):
            ctx.state.cache["hit"] = True
            return PluginResponse.passthrough()

    class CacheReader(BasePlugin):
        name = "cache_reader"
        hook = PluginHook.PRE_FLIGHT
        async def execute(self, ctx):
            assert ctx.state.cache.get("hit") is True
            assert ctx.state.config["flag"] is True
            return PluginResponse.passthrough()

    manager = PluginManager(plugins_dir="plugins")
    writer = CacheWriter()
    reader = CacheReader()
    manager.rings[PluginHook.PRE_FLIGHT] = [
        {"type": "class", "instance": writer, "name": "cache_writer", "timeout_ms": 100, "fail_policy": "open"},
        {"type": "class", "instance": reader, "name": "cache_reader", "timeout_ms": 100, "fail_policy": "open"},
    ]
    manager._init_stats("cache_writer")
    manager._init_stats("cache_reader")

    ctx = PluginContext(body={}, state=state)
    await manager.execute_ring(PluginHook.PRE_FLIGHT, ctx)

    assert ctx.state.cache["hit"] is True
    assert ctx.stop_chain is False

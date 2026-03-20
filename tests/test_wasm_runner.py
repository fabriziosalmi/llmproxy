"""
Tests for LLMPROXY WASM Plugin Runner.

All tests are mock-based — no Rust toolchain or .wasm files required.
Tests verify:
  - JSON protocol (input serialization, output parsing)
  - Action mapping (WASM legacy ALLOW/BLOCK/MODIFIED → PluginResponse)
  - Error handling (invalid JSON, missing extism, unloaded runner)
  - Async execution model (to_thread delegation)
  - Integration with plugin engine execute_ring
"""

import json
import asyncio
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from core.plugin_sdk import PluginResponse, PluginAction
from core.wasm_runner import WasmRunner
from core.plugin_engine import PluginContext, PluginManager


# ── WasmRunner Unit Tests (mock extism) ──


@pytest.fixture
def mock_runner():
    """Create a WasmRunner with mocked internals (no real .wasm file)."""
    runner = WasmRunner(wasm_path="plugins/wasm/fake.wasm", config={"threshold": 0.5})
    runner._loaded = True
    runner._plugin = MagicMock()
    return runner


@pytest.mark.asyncio
async def test_wasm_passthrough(mock_runner):
    """WASM returning passthrough action should create passthrough response."""
    output = json.dumps({"action": "passthrough"}).encode()
    mock_runner._plugin.call = MagicMock(return_value=output)

    result = await mock_runner.execute(
        body={"messages": [{"role": "user", "content": "Hello"}]},
        session_id="sess1",
    )
    assert result.action == "passthrough"


@pytest.mark.asyncio
async def test_wasm_block(mock_runner):
    """WASM returning block action should create block response."""
    output = json.dumps({
        "action": "block",
        "status_code": 403,
        "error_type": "injection_detected",
        "message": "Injection blocked",
    }).encode()
    mock_runner._plugin.call = MagicMock(return_value=output)

    result = await mock_runner.execute(body={})
    assert result.action == "block"
    assert result.status_code == 403
    assert result.error_type == "injection_detected"
    assert result.message == "Injection blocked"


@pytest.mark.asyncio
async def test_wasm_modify(mock_runner):
    """WASM returning modify action should include modified body."""
    modified_body = {"messages": [{"role": "user", "content": "[REDACTED]"}]}
    output = json.dumps({
        "action": "modify",
        "body": modified_body,
    }).encode()
    mock_runner._plugin.call = MagicMock(return_value=output)

    result = await mock_runner.execute(body={"messages": [{"role": "user", "content": "secret"}]})
    assert result.action == "modify"
    assert result.body == modified_body


@pytest.mark.asyncio
async def test_wasm_legacy_allow_mapped(mock_runner):
    """Legacy WASM 'ALLOW' action should map to 'passthrough'."""
    output = json.dumps({"action": "ALLOW"}).encode()
    mock_runner._plugin.call = MagicMock(return_value=output)

    result = await mock_runner.execute(body={})
    assert result.action == "passthrough"


@pytest.mark.asyncio
async def test_wasm_legacy_modified_mapped(mock_runner):
    """Legacy WASM 'MODIFIED' action with clean_prompt should map to 'modify'."""
    output = json.dumps({
        "action": "MODIFIED",
        "clean_prompt": "sanitized text",
    }).encode()
    mock_runner._plugin.call = MagicMock(return_value=output)

    result = await mock_runner.execute(body={})
    assert result.action == "modify"
    assert result.body == {"_wasm_clean_prompt": "sanitized text"}


@pytest.mark.asyncio
async def test_wasm_legacy_block_with_reason(mock_runner):
    """Legacy WASM BLOCK with 'reason' field should map to message."""
    output = json.dumps({
        "action": "BLOCK",
        "reason": "System prompt injection detected",
    }).encode()
    mock_runner._plugin.call = MagicMock(return_value=output)

    result = await mock_runner.execute(body={})
    assert result.action == "block"
    assert "injection" in result.message.lower()


@pytest.mark.asyncio
async def test_wasm_invalid_json_passthrough(mock_runner):
    """Invalid JSON from WASM should return passthrough (fail-open)."""
    mock_runner._plugin.call = MagicMock(return_value=b"not json at all")

    result = await mock_runner.execute(body={})
    assert result.action == "passthrough"


@pytest.mark.asyncio
async def test_wasm_empty_output_passthrough(mock_runner):
    """Empty/None output from WASM should return passthrough."""
    mock_runner._plugin.call = MagicMock(return_value=None)

    result = await mock_runner.execute(body={})
    assert result.action == "passthrough"


@pytest.mark.asyncio
async def test_wasm_unknown_action_passthrough(mock_runner):
    """Unknown action from WASM should return passthrough."""
    output = json.dumps({"action": "banana"}).encode()
    mock_runner._plugin.call = MagicMock(return_value=output)

    result = await mock_runner.execute(body={})
    assert result.action == "passthrough"


@pytest.mark.asyncio
async def test_wasm_unloaded_runner():
    """Unloaded runner should return passthrough without error."""
    runner = WasmRunner(wasm_path="fake.wasm")
    # Not loaded — _loaded is False by default

    result = await runner.execute(body={"test": True})
    assert result.action == "passthrough"


@pytest.mark.asyncio
async def test_wasm_exception_passthrough(mock_runner):
    """Exception during WASM call should return passthrough (fail-open)."""
    mock_runner._plugin.call = MagicMock(side_effect=RuntimeError("WASM crashed"))

    result = await mock_runner.execute(body={})
    assert result.action == "passthrough"


@pytest.mark.asyncio
async def test_wasm_input_contains_config(mock_runner):
    """Input JSON sent to WASM should include plugin config."""
    captured_input = {}

    def mock_call(fn_name, data):
        captured_input.update(json.loads(data.decode()))
        return json.dumps({"action": "passthrough"}).encode()

    mock_runner._plugin.call = mock_call

    await mock_runner.execute(
        body={"messages": []},
        metadata={"api_key": "sk-test"},
        session_id="sess42",
    )

    assert captured_input["config"] == {"threshold": 0.5}
    assert captured_input["session_id"] == "sess42"
    assert captured_input["metadata"]["api_key"] == "sk-test"


# ── Plugin Engine WASM Integration Tests ──


@pytest.mark.asyncio
async def test_engine_wasm_block_stops_chain():
    """WASM plugin returning block should stop the chain in engine."""
    from core.plugin_engine import PluginHook as EngineHook

    mock_runner = MagicMock()
    mock_runner.execute = AsyncMock(return_value=PluginResponse.block(
        status_code=403, error_type="wasm_injection", message="Blocked by WASM"
    ))

    manager = PluginManager(plugins_dir="plugins")
    manager.rings[EngineHook.INGRESS] = [{
        "type": "wasm",
        "path": "fake.wasm",
        "name": "wasm_test",
        "timeout_ms": 100,
        "fail_policy": "closed",
        "_runner": mock_runner,
    }]
    manager._init_stats("wasm_test")

    ctx = PluginContext(body={"messages": [{"role": "user", "content": "test"}]})
    await manager.execute_ring(EngineHook.INGRESS, ctx)

    assert ctx.stop_chain is True
    assert ctx.error == "Blocked by WASM"
    assert ctx.metadata["_block_status"] == 403
    stats = manager.get_plugin_stats("wasm_test")
    assert stats["blocks"] == 1


@pytest.mark.asyncio
async def test_engine_wasm_modify_updates_body():
    """WASM plugin returning modify should update context body."""
    from core.plugin_engine import PluginHook as EngineHook

    new_body = {"messages": [{"role": "user", "content": "[REDACTED]"}]}
    mock_runner = MagicMock()
    mock_runner.execute = AsyncMock(return_value=PluginResponse.modify(body=new_body))

    manager = PluginManager(plugins_dir="plugins")
    manager.rings[EngineHook.PRE_FLIGHT] = [{
        "type": "wasm",
        "path": "fake.wasm",
        "name": "wasm_pii",
        "timeout_ms": 100,
        "fail_policy": "open",
        "_runner": mock_runner,
    }]
    manager._init_stats("wasm_pii")

    ctx = PluginContext(body={"messages": [{"role": "user", "content": "my email is test@test.com"}]})
    await manager.execute_ring(EngineHook.PRE_FLIGHT, ctx)

    assert ctx.body == new_body
    assert ctx.stop_chain is False


@pytest.mark.asyncio
async def test_engine_wasm_no_runner_noop():
    """WASM plugin with no runner (extism not installed) should be a no-op."""
    from core.plugin_engine import PluginHook as EngineHook

    manager = PluginManager(plugins_dir="plugins")
    manager.rings[EngineHook.BACKGROUND] = [{
        "type": "wasm",
        "path": "fake.wasm",
        "name": "wasm_missing",
        "timeout_ms": 100,
        "fail_policy": "open",
        "_runner": None,  # Extism not installed
    }]
    manager._init_stats("wasm_missing")

    ctx = PluginContext(body={"test": True})
    await manager.execute_ring(EngineHook.BACKGROUND, ctx)

    # Should not crash, should not modify context
    assert ctx.stop_chain is False
    assert ctx.error is None

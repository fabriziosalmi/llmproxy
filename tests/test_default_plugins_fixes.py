import json
import pytest
from fastapi.responses import Response
from core.plugin_engine import PluginContext
from plugins.default.context_minifier import compress
from plugins.default.json_healer import repair
from plugins.default.kill_switch import analyze
from plugins.default.shield_sanitizer import cleanse

class MockRotator:
    def __init__(self):
        self.logs = []
        self.security = MockSecurity()
    async def _add_log(self, msg, level="INFO"):
        self.logs.append((msg, level))

class MockSecurity:
    def sanitize_response(self, text):
        if "forbidden" in text:
            return "[SEC_ERR: Blocked]"
        return text

@pytest.mark.asyncio
async def test_context_minifier_url_preservation():
    # Test text prompt
    body = {
        "messages": [
            {"role": "user", "content": "Hello! Check this websocket connection: ws://localhost:8080/ws\n" + "// comment\n" + "x = 10\n" * 150}
        ]
    }
    rotator = MockRotator()
    ctx = PluginContext(body=body, metadata={"rotator": rotator})
    await compress(ctx)
    content = body["messages"][-1]["content"]
    assert "ws://localhost:8080/ws" in content
    assert "// comment" not in content

@pytest.mark.asyncio
async def test_context_minifier_multimodal_list():
    body = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Check this DB URL: postgres://user:pass@host:5432/db\n" + "// line comment\n" + "y = 20\n" * 200}
                ]
            }
        ]
    }
    rotator = MockRotator()
    ctx = PluginContext(body=body, metadata={"rotator": rotator})
    await compress(ctx)
    text = body["messages"][-1]["content"][0]["text"]
    assert "postgres://user:pass@host:5432/db" in text
    assert "// line comment" not in text

@pytest.mark.asyncio
async def test_json_healer_nested_and_list():
    # Nested JSON healing
    response = Response(
        content=b'[ {"a": [1, 2',
        status_code=200,
        headers={"x-custom-header": "test-val", "content-length": "13"},
        media_type="application/json"
    )
    rotator = MockRotator()
    ctx = PluginContext(response=response, metadata={"rotator": rotator})
    await repair(ctx)
    
    assert ctx.response is not None
    data = json.loads(ctx.response.body.decode())
    assert data == [{"a": [1, 2]}]
    assert ctx.response.headers["x-custom-header"] == "test-val"
    assert "content-length" not in ctx.response.headers or int(ctx.response.headers["content-length"]) > 13

@pytest.mark.asyncio
async def test_kill_switch_no_mutation_crash():
    # Loop that should trigger kill switch
    loop_text = "repeat " * 20
    response = Response(
        content=loop_text.encode(),
        status_code=200,
        headers={"x-custom-header": "kill-switch-val"},
        media_type="application/json"
    )
    rotator = MockRotator()
    ctx = PluginContext(response=response, metadata={"rotator": rotator})
    
    # Run analyzer. It should NOT raise AttributeError: can't set attribute.
    await analyze(ctx)
    
    assert ctx.response.body == b" [LLMPROXY_ERROR: INFINITE_LOOP_DETECTED_SNIPPED]"
    assert ctx.response.headers["x-custom-header"] == "kill-switch-val"

@pytest.mark.asyncio
async def test_shield_sanitizer_headers():
    response = Response(
        content=json.dumps({"choices": [{"message": {"content": "this is fine"}}]}).encode(),
        status_code=200,
        headers={"x-custom-header": "sanitizer-val"},
        media_type="application/json"
    )
    rotator = MockRotator()
    ctx = PluginContext(response=response, metadata={"rotator": rotator})
    await cleanse(ctx)
    
    assert ctx.response.headers["x-custom-header"] == "sanitizer-val"

"""Tests for v1.6.0 marketplace plugins: ToolGuard, TenantQoSRouter, SchemaEnforcer, ShadowTraffic."""

import pytest
from core.plugin_engine import PluginContext, PluginState


# ── ToolGuard ──

class TestToolGuard:

    def _make_ctx(self, tools=None, roles=None):
        body = {"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]}
        if tools:
            body["tools"] = tools
        return PluginContext(
            body=body, session_id="test", metadata={"_user_roles": roles or []}, state=PluginState()
        )

    def _tool(self, name):
        return {"type": "function", "function": {"name": name, "parameters": {}}}

    @pytest.mark.asyncio
    async def test_no_tools_passthrough(self):
        from plugins.marketplace.tool_guard import ToolGuard
        p = ToolGuard(config={"restricted_tools": ["dangerous"]})
        ctx = self._make_ctx()
        r = await p.execute(ctx)
        assert r.action == "passthrough"

    @pytest.mark.asyncio
    async def test_admin_bypasses(self):
        from plugins.marketplace.tool_guard import ToolGuard
        p = ToolGuard(config={"restricted_tools": ["dangerous"], "admin_roles": ["admin"]})
        ctx = self._make_ctx(tools=[self._tool("dangerous")], roles=["admin"])
        r = await p.execute(ctx)
        assert r.action == "passthrough"

    @pytest.mark.asyncio
    async def test_strip_restricted_tool(self):
        from plugins.marketplace.tool_guard import ToolGuard
        p = ToolGuard(config={"restricted_tools": ["dangerous"], "action": "strip"})
        ctx = self._make_ctx(tools=[self._tool("safe"), self._tool("dangerous")], roles=["user"])
        r = await p.execute(ctx)
        assert r.action == "modify"
        assert len(ctx.body["tools"]) == 1
        assert ctx.body["tools"][0]["function"]["name"] == "safe"

    @pytest.mark.asyncio
    async def test_block_restricted_tool(self):
        from plugins.marketplace.tool_guard import ToolGuard
        p = ToolGuard(config={"restricted_tools": ["dangerous"], "action": "block"})
        ctx = self._make_ctx(tools=[self._tool("dangerous")], roles=["user"])
        r = await p.execute(ctx)
        assert r.action == "block"
        assert r.status_code == 403

    @pytest.mark.asyncio
    async def test_unrestricted_tools_pass(self):
        from plugins.marketplace.tool_guard import ToolGuard
        p = ToolGuard(config={"restricted_tools": ["dangerous"]})
        ctx = self._make_ctx(tools=[self._tool("safe"), self._tool("also_safe")], roles=["user"])
        r = await p.execute(ctx)
        assert r.action == "passthrough"


# ── TenantQoSRouter ──

class TestTenantQoSRouter:

    def _make_ctx(self, model="gpt-4o", roles=None, tier=None):
        meta = {"_user_roles": roles or []}
        if tier:
            meta["_tenant_tier"] = tier
        return PluginContext(
            body={"model": model, "messages": [{"role": "user", "content": "hi"}]},
            session_id="test", metadata=meta, state=PluginState()
        )

    @pytest.mark.asyncio
    async def test_free_tier_downgrade(self):
        from plugins.marketplace.tenant_qos_router import TenantQoSRouter
        p = TenantQoSRouter(config={"tier_mapping": {"free": "gpt-4o-mini"}, "default_tier": "free"})
        ctx = self._make_ctx(roles=["viewer"])
        r = await p.execute(ctx)
        assert r.action == "modify"
        assert ctx.body["model"] == "gpt-4o-mini"

    @pytest.mark.asyncio
    async def test_premium_passthrough(self):
        from plugins.marketplace.tenant_qos_router import TenantQoSRouter
        p = TenantQoSRouter(config={"tier_mapping": {"premium": ""}, "default_tier": "free"})
        ctx = self._make_ctx(roles=["admin"])
        r = await p.execute(ctx)
        assert r.action == "passthrough"

    @pytest.mark.asyncio
    async def test_explicit_tier_override(self):
        from plugins.marketplace.tenant_qos_router import TenantQoSRouter
        p = TenantQoSRouter(config={"tier_mapping": {"gold": "gpt-4o"}, "default_tier": "free"})
        ctx = self._make_ctx(tier="gold")
        r = await p.execute(ctx)
        assert r.action == "passthrough"  # gpt-4o -> gpt-4o, no change

    @pytest.mark.asyncio
    async def test_force_downgrade_off(self):
        from plugins.marketplace.tenant_qos_router import TenantQoSRouter
        p = TenantQoSRouter(config={"force_downgrade": False})
        ctx = self._make_ctx(roles=["viewer"])
        r = await p.execute(ctx)
        assert r.action == "passthrough"


# ── SchemaEnforcer ──

class TestSchemaEnforcer:

    def _make_ctx(self, content="", schema=None):
        body = {"choices": [{"message": {"content": content}}]}
        meta = {}
        if schema:
            import json
            meta["_expected_schema"] = json.dumps(schema)
        return PluginContext(body=body, session_id="test", metadata=meta, state=PluginState())

    @pytest.mark.asyncio
    async def test_no_schema_passthrough(self):
        from plugins.marketplace.schema_enforcer import SchemaEnforcer
        p = SchemaEnforcer()
        ctx = self._make_ctx(content='{"name": "test"}')
        r = await p.execute(ctx)
        assert r.action == "passthrough"

    @pytest.mark.asyncio
    async def test_valid_schema_passes(self):
        from plugins.marketplace.schema_enforcer import SchemaEnforcer
        p = SchemaEnforcer()
        schema = {"type": "object", "required": ["name"], "properties": {"name": {"type": "string"}}}
        ctx = self._make_ctx(content='{"name": "test"}', schema=schema)
        r = await p.execute(ctx)
        assert r.action == "passthrough"

    @pytest.mark.asyncio
    async def test_missing_required_field_warn(self):
        from plugins.marketplace.schema_enforcer import SchemaEnforcer
        p = SchemaEnforcer(config={"action": "warn"})
        schema = {"type": "object", "required": ["name", "age"]}
        ctx = self._make_ctx(content='{"name": "test"}', schema=schema)
        r = await p.execute(ctx)
        assert r.action == "passthrough"  # warn mode passes through
        assert "_schema_errors" in ctx.metadata

    @pytest.mark.asyncio
    async def test_missing_required_field_block(self):
        from plugins.marketplace.schema_enforcer import SchemaEnforcer
        p = SchemaEnforcer(config={"action": "block"})
        schema = {"type": "object", "required": ["name", "age"]}
        ctx = self._make_ctx(content='{"name": "test"}', schema=schema)
        r = await p.execute(ctx)
        assert r.action == "block"
        assert r.status_code == 422

    @pytest.mark.asyncio
    async def test_wrong_type_detected(self):
        from plugins.marketplace.schema_enforcer import SchemaEnforcer
        p = SchemaEnforcer(config={"action": "block"})
        schema = {"type": "object", "properties": {"age": {"type": "integer"}}}
        ctx = self._make_ctx(content='{"age": "not_a_number"}', schema=schema)
        r = await p.execute(ctx)
        assert r.action == "block"

    @pytest.mark.asyncio
    async def test_non_json_response_passthrough(self):
        from plugins.marketplace.schema_enforcer import SchemaEnforcer
        p = SchemaEnforcer(config={"action": "block"})
        schema = {"type": "object"}
        ctx = self._make_ctx(content="Hello, this is plain text", schema=schema)
        r = await p.execute(ctx)
        assert r.action == "passthrough"  # not JSON, skip


# ── ShadowTraffic ──

class TestShadowTraffic:

    @pytest.mark.asyncio
    async def test_no_shadow_model_passthrough(self):
        from plugins.marketplace.shadow_traffic import ShadowTraffic
        p = ShadowTraffic(config={"shadow_model": ""})
        ctx = PluginContext(
            body={"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
            session_id="test", metadata={}, state=PluginState()
        )
        r = await p.execute(ctx)
        assert r.action == "passthrough"

    @pytest.mark.asyncio
    async def test_sample_rate_zero_skips(self):
        from plugins.marketplace.shadow_traffic import ShadowTraffic
        p = ShadowTraffic(config={"shadow_model": "claude-sonnet", "sample_rate": 0.0})
        ctx = PluginContext(
            body={"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
            session_id="test", metadata={}, state=PluginState()
        )
        # With sample_rate=0.0, random.random() > 0.0 is always true, so always skipped
        r = await p.execute(ctx)
        assert r.action == "passthrough"

    @pytest.mark.asyncio
    async def test_no_messages_passthrough(self):
        from plugins.marketplace.shadow_traffic import ShadowTraffic
        p = ShadowTraffic(config={"shadow_model": "claude-sonnet", "sample_rate": 1.0})
        ctx = PluginContext(
            body={"model": "gpt-4o", "messages": []},
            session_id="test", metadata={}, state=PluginState()
        )
        r = await p.execute(ctx)
        assert r.action == "passthrough"

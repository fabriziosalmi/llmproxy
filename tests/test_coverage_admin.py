"""
Coverage tests for proxy/routes/admin.py — 159 uncovered lines.

Tests admin endpoints via httpx AsyncClient against a mock agent.
"""

import pytest
from unittest.mock import MagicMock, AsyncMock
from httpx import AsyncClient, ASGITransport
from fastapi import FastAPI

from tests.conftest import minimal_config


def _make_admin_app():
    """Build minimal FastAPI app with admin routes."""
    from proxy.routes.admin import create_router

    agent = MagicMock()
    agent.config = minimal_config(auth_enabled=False)
    agent.config["budget"] = {"daily_limit": 50.0, "soft_limit": 40.0}
    agent.config["rate_limiting"] = {"enabled": False, "requests_per_minute": 60}

    agent.store = AsyncMock()
    agent.store.set_state = AsyncMock()
    agent.store.query_spend = AsyncMock(return_value=[])
    agent.store.get_spend_total = AsyncMock(return_value=0.0)
    agent.store.verify_audit_chain = AsyncMock(return_value={"valid": True, "total": 0, "verified": 0})
    agent.store.query_audit = AsyncMock(return_value=[])

    agent.proxy_enabled = True
    agent.priority_mode = False
    agent.features = {"language_guard": True, "injection_guard": True, "link_sanitizer": True}
    agent.total_cost_today = 5.67
    agent._budget_date = "2026-03-25"
    agent._add_log = AsyncMock()
    agent._get_api_keys = MagicMock(return_value=["test-key"])
    agent.exporter = None
    agent.webhooks = MagicMock()
    agent.webhooks.enabled = False
    agent.webhooks.endpoints = []
    agent.webhooks.dispatch = AsyncMock()

    agent.security = MagicMock()
    agent.security.config = {}

    agent.circuit_manager = MagicMock()
    agent.circuit_manager.get_all_states.return_value = {}

    agent.negative_cache = MagicMock()
    agent.negative_cache.stats.return_value = {"size": 0, "hits": 0}

    agent.cache_backend = AsyncMock()
    agent.cache_backend.stats = AsyncMock(return_value={"size": 0, "hits": 0, "misses": 0})

    agent.rbac = MagicMock()
    agent.rbac.permissions = {"admin": {"*"}, "user": {"chat"}}

    agent.plugin_manager = MagicMock()
    agent.plugin_manager.get_plugin_stats.return_value = {}
    agent.plugin_manager.get_ring_latency.return_value = {}
    agent.plugin_manager.get_ring_traces.return_value = []
    agent.plugin_manager._ring_traces = []

    app = FastAPI()
    app.include_router(create_router(agent))
    return app, agent


class TestAdminRoutes:

    @pytest.mark.asyncio
    async def test_get_proxy_status(self):
        app, agent = _make_admin_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/v1/proxy/status")
        assert resp.status_code == 200
        assert resp.json()["enabled"] is True

    @pytest.mark.asyncio
    async def test_toggle_proxy(self):
        app, agent = _make_admin_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/api/v1/proxy/toggle", json={"enabled": False})
        assert resp.status_code == 200
        assert resp.json()["enabled"] is False
        agent.store.set_state.assert_called()

    @pytest.mark.asyncio
    async def test_get_version(self):
        app, agent = _make_admin_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/v1/version")
        assert resp.status_code == 200
        assert "version" in resp.json()

    @pytest.mark.asyncio
    async def test_get_service_info(self):
        app, agent = _make_admin_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/v1/service-info")
        assert resp.status_code == 200
        data = resp.json()
        assert "port" in data
        assert "url" in data

    @pytest.mark.asyncio
    async def test_get_features(self):
        app, agent = _make_admin_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/v1/features")
        assert resp.status_code == 200
        data = resp.json()
        assert "language_guard" in data
        assert "injection_guard" in data

    @pytest.mark.asyncio
    async def test_toggle_feature(self):
        app, agent = _make_admin_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/api/v1/features/toggle", json={"name": "language_guard", "enabled": False})
        assert resp.status_code == 200
        assert resp.json()["enabled"] is False

    @pytest.mark.asyncio
    async def test_toggle_unknown_feature(self):
        app, agent = _make_admin_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/api/v1/features/toggle", json={"name": "nonexistent"})
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_toggle_priority_mode(self):
        app, agent = _make_admin_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/api/v1/proxy/priority/toggle", json={"enabled": True})
        assert resp.status_code == 200
        assert resp.json()["enabled"] is True

    @pytest.mark.asyncio
    async def test_get_network_info(self):
        app, agent = _make_admin_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/v1/network/info")
        assert resp.status_code == 200
        assert "tailscale_active" in resp.json()

    @pytest.mark.asyncio
    async def test_guards_status(self):
        app, agent = _make_admin_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/v1/guards/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "features" in data
        assert "firewall" in data
        assert "budget" in data

    @pytest.mark.asyncio
    async def test_export_status_disabled(self):
        app, agent = _make_admin_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/v1/export/status")
        assert resp.status_code == 200
        assert resp.json()["enabled"] is False

    @pytest.mark.asyncio
    async def test_rbac_roles(self):
        app, agent = _make_admin_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/v1/rbac/roles")
        assert resp.status_code == 200
        data = resp.json()
        assert "admin" in data

    @pytest.mark.asyncio
    async def test_latency_metrics(self):
        app, agent = _make_admin_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/v1/metrics/latency")
        assert resp.status_code == 200
        data = resp.json()
        assert "rings" in data
        assert "plugins" in data
        assert "ttft" in data

    @pytest.mark.asyncio
    async def test_ring_timeline(self):
        app, agent = _make_admin_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/v1/metrics/ring-timeline")
        assert resp.status_code == 200
        assert "traces" in resp.json()

    @pytest.mark.asyncio
    async def test_cache_stats(self):
        app, agent = _make_admin_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/v1/cache/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert "negative_cache" in data
        assert "positive_cache" in data

    @pytest.mark.asyncio
    async def test_panic_endpoint(self):
        app, agent = _make_admin_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/api/v1/panic")
        assert resp.status_code == 200
        assert resp.json()["status"] == "HALTED"
        assert agent.proxy_enabled is False

    @pytest.mark.asyncio
    async def test_analytics_spend(self):
        app, agent = _make_admin_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/v1/analytics/spend")
        assert resp.status_code == 200
        data = resp.json()
        assert "total" in data
        assert "breakdown" in data

    @pytest.mark.asyncio
    async def test_analytics_top_models(self):
        app, agent = _make_admin_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/v1/analytics/spend/topmodels")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_analytics_cost_efficiency(self):
        app, agent = _make_admin_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/v1/analytics/cost-efficiency")
        assert resp.status_code == 200
        data = resp.json()
        assert "period_total_usd" in data

    @pytest.mark.asyncio
    async def test_audit_verify(self):
        app, agent = _make_admin_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/v1/audit/verify")
        assert resp.status_code == 200
        assert resp.json()["valid"] is True

    @pytest.mark.asyncio
    async def test_audit_query(self):
        app, agent = _make_admin_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/v1/audit")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_webhooks_list(self):
        app, agent = _make_admin_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/v1/webhooks")
        assert resp.status_code == 200
        data = resp.json()
        assert "enabled" in data
        assert "endpoints" in data

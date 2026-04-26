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
    agent.routing_cost_weight = 0.3
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
        # K.1: spend now surfaces the active routing config so dashboards can
        # show the cost-bias setting alongside the spend numbers.
        assert data["routing"] == {
            "cost_weight": 0.3,
            "priority_mode": False,
            "strategy": "smart_weighted",
        }

    @pytest.mark.asyncio
    async def test_get_routing_config(self):
        app, agent = _make_admin_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/v1/routing/config")
        assert resp.status_code == 200
        assert resp.json() == {
            "cost_weight": 0.3,
            "priority_mode": False,
            "strategy": "smart_weighted",
        }

    @pytest.mark.asyncio
    async def test_get_routing_config_strategy_priority(self):
        app, agent = _make_admin_app()
        agent.priority_mode = True
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/v1/routing/config")
        assert resp.json()["strategy"] == "priority"

    @pytest.mark.asyncio
    async def test_get_routing_config_strategy_performance(self):
        app, agent = _make_admin_app()
        agent.routing_cost_weight = 0.0
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/v1/routing/config")
        assert resp.json()["strategy"] == "performance"

    @pytest.mark.asyncio
    async def test_set_routing_cost_weight_valid(self):
        app, agent = _make_admin_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/api/v1/routing/cost-weight", json={"cost_weight": 0.7})
        assert resp.status_code == 200
        assert resp.json()["cost_weight"] == 0.7
        assert agent.routing_cost_weight == 0.7
        agent.store.set_state.assert_awaited_with("routing:cost_weight", 0.7)

    @pytest.mark.asyncio
    async def test_set_routing_cost_weight_zero(self):
        app, agent = _make_admin_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/api/v1/routing/cost-weight", json={"cost_weight": 0.0})
        assert resp.status_code == 200
        # 0.0 must round-trip — the smart_router fallback uses `is None`
        # specifically so 0.0 (ignore cost) is honored.
        assert agent.routing_cost_weight == 0.0
        assert resp.json()["strategy"] == "performance"

    @pytest.mark.asyncio
    async def test_set_routing_cost_weight_out_of_range(self):
        app, agent = _make_admin_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp_high = await c.post("/api/v1/routing/cost-weight", json={"cost_weight": 1.5})
            resp_low = await c.post("/api/v1/routing/cost-weight", json={"cost_weight": -0.1})
        assert resp_high.status_code == 400
        assert resp_low.status_code == 400

    @pytest.mark.asyncio
    async def test_set_routing_cost_weight_invalid_type(self):
        app, agent = _make_admin_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/api/v1/routing/cost-weight", json={"cost_weight": "fast"})
        assert resp.status_code == 400

    # ── N.6 Rate-limit presets ───────────────────────────────────────

    @pytest.mark.asyncio
    async def test_rate_limit_config_returns_presets_when_no_instance(self):
        """With the middleware not constructed (test app), fall back to
        config + still expose the presets so the UI can render them."""
        from core.rate_limiter import RateLimitMiddleware
        RateLimitMiddleware.instance = None  # ensure clean state
        app, agent = _make_admin_app()
        agent.config["rate_limiting"] = {"enabled": True, "requests_per_minute": 90, "burst": 12}
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/v1/rate-limit/config")
        assert resp.status_code == 200
        body = resp.json()
        assert body["enabled"] is True
        assert body["requests_per_minute"] == 90
        assert body["burst"] == 12
        assert body["preset"] is None
        # Presets always included so the UI can render the radio group.
        assert set(body["presets"]) == {"strict", "normal", "relaxed"}
        assert body["presets"]["strict"]["requests_per_minute"] == 30

    @pytest.mark.asyncio
    async def test_set_rate_limit_preset_503_when_middleware_missing(self):
        from core.rate_limiter import RateLimitMiddleware
        RateLimitMiddleware.instance = None
        app, agent = _make_admin_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/api/v1/rate-limit/preset", json={"preset": "strict"})
        assert resp.status_code == 503

    @pytest.mark.asyncio
    async def test_set_rate_limit_preset_invalid_name(self):
        app, agent = _make_admin_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/api/v1/rate-limit/preset", json={"preset": "paranoid"})
        assert resp.status_code == 400
        assert "Unknown preset" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_set_rate_limit_preset_applies_and_persists(self):
        """Happy path: middleware exists, preset is valid, limiter mutates,
        store records the preset name."""
        from core.rate_limiter import RateLimitMiddleware
        # Spin up a real middleware so .instance is set + apply_preset works.
        from starlette.applications import Starlette
        mw = RateLimitMiddleware(app=Starlette(), config={"rate_limiting": {"enabled": True}})
        try:
            app, agent = _make_admin_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/v1/rate-limit/preset", json={"preset": "strict"})
            assert resp.status_code == 200
            body = resp.json()
            assert body["preset"] == "strict"
            assert body["requests_per_minute"] == 30
            assert body["burst"] == 5
            # Limiter mutated.
            assert mw.limiter.default_capacity == 35
            assert mw.limiter.default_rate == pytest.approx(30 / 60)
            # Persisted.
            agent.store.set_state.assert_awaited_with("rate_limit:preset", "strict")
        finally:
            RateLimitMiddleware.instance = None

    # ── O.5 — /api/v1/config/yaml ─────────────────────────────────

    @pytest.mark.asyncio
    async def test_config_yaml_serialises_active_config(self):
        app, agent = _make_admin_app()
        # Add a non-trivial config shape so we can assert it round-trips.
        agent.config["routing"] = {"cost_weight": 0.4, "strategy": "smart_weighted"}
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/v1/config/yaml")
        assert resp.status_code == 200
        body = resp.json()
        assert "yaml" in body
        # Sections we set in the fixture must appear in the rendered YAML.
        assert "routing:" in body["yaml"]
        assert "cost_weight: 0.4" in body["yaml"]
        assert "smart_weighted" in body["yaml"]

    # ── Q.3 — /api/v1/metrics/hourly-buckets ─────────────────────

    @pytest.mark.asyncio
    async def test_hourly_buckets_returns_default_shape_when_no_history(self):
        """Proxy boot — agent has no metrics_history yet. Endpoint must
        still respond with a stable shape so the UI consumer doesn't
        need a special-case for 'before first hour'."""
        app, agent = _make_admin_app()
        agent.metrics_history = None
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/v1/metrics/hourly-buckets")
        assert resp.status_code == 200
        body = resp.json()
        assert body["hours"] == 24
        assert body["interval_s"] == 3600
        assert body["series"] == {}

    @pytest.mark.asyncio
    async def test_hourly_buckets_serialises_history_snapshot(self):
        """With a real MetricsHistory wired, every series surfaces."""
        from core.metrics_history import MetricsHistory
        app, agent = _make_admin_app()
        h = MetricsHistory(slots=12)
        # Seed three series with deterministic values.
        h.record_delta("requests", 1000)
        h.record_delta("requests", 1042)
        h.record_gauge("cost_usd", 5.67)
        agent.metrics_history = h

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/v1/metrics/hourly-buckets")
        body = resp.json()
        assert body["hours"] == 12
        assert "requests" in body["series"]
        assert "cost_usd" in body["series"]
        # Each series the length of the buffer (pre-fill is part of the contract).
        assert len(body["series"]["requests"]) == 12
        assert body["series"]["requests"][-1] == 42.0  # last delta
        assert body["series"]["cost_usd"][-1] == pytest.approx(5.67)

    @pytest.mark.asyncio
    async def test_openapi_endpoint_returns_schema(self):
        """Q.2 — auth-gated mirror of /openapi.json. The default FastAPI
        route is disabled when auth is on (proxy/app_factory.py:86), so
        this endpoint is the way an authenticated operator gets at the
        schema for Swagger / Postman / openapi-generator."""
        app, agent = _make_admin_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/v1/openapi.json")
        assert resp.status_code == 200
        body = resp.json()
        # Shape contract — Swagger / Postman / openapi-generator all read
        # info + paths off the root.
        assert "openapi" in body
        assert "info" in body
        assert "paths" in body
        # The endpoint we just added must be in its own schema (sanity).
        assert "/api/v1/openapi.json" in body["paths"]

    @pytest.mark.asyncio
    async def test_config_yaml_redacts_secrets(self):
        """O.5 — secrets must be scrubbed before serialisation. A leaked
        screenshot of the Settings panel can't leak credentials."""
        app, agent = _make_admin_app()
        agent.config["secrets"] = {
            "api_key": "sk-real-secret-12345",
            "authorization": "Bearer leaked-token",
            "password": "p@ssw0rd",
        }
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/v1/config/yaml")
        body = resp.json()
        rendered = body["yaml"]
        assert "sk-real-secret-12345" not in rendered
        assert "Bearer leaked-token" not in rendered
        assert "p@ssw0rd" not in rendered
        assert "<REDACTED>" in rendered

    @pytest.mark.asyncio
    async def test_set_rate_limit_preset_flushes_existing_buckets(self):
        """A bucket created at the OLD rate must be evicted so the next
        request from that key gets the new defaults — otherwise an attacker
        spraying during the cutover keeps their old (relaxed) cap."""
        from core.rate_limiter import RateLimitMiddleware
        from starlette.applications import Starlette
        mw = RateLimitMiddleware(app=Starlette(), config={
            "rate_limiting": {"enabled": True, "requests_per_minute": 240, "burst": 60}
        })
        try:
            # Seed a bucket at the relaxed defaults.
            await mw.limiter.check("1.2.3.4")
            assert "1.2.3.4" in mw.limiter._buckets

            app, agent = _make_admin_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                await c.post("/api/v1/rate-limit/preset", json={"preset": "strict"})

            assert "1.2.3.4" not in mw.limiter._buckets
        finally:
            RateLimitMiddleware.instance = None

    # ── M.2 Spend forecasting ───────────────────────────────────────

    @pytest.mark.asyncio
    async def test_forecast_endpoint_returns_block(self):
        app, agent = _make_admin_app()
        # Fixture has total_cost_today=5.67, daily_limit=50.0
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/v1/analytics/forecast")
        assert resp.status_code == 200
        body = resp.json()
        # Shape contract — operators consume these names directly.
        assert set(body.keys()) == {
            "current_spend_usd",
            "daily_limit_usd",
            "elapsed_hours",
            "burn_rate_usd_per_hour",
            "projected_daily_total_usd",
            "headroom_usd",
            "time_to_limit_hours",
        }
        assert body["current_spend_usd"] == pytest.approx(5.67)
        assert body["daily_limit_usd"] == pytest.approx(50.0)

    @pytest.mark.asyncio
    async def test_spend_endpoint_embeds_forecast_block(self):
        app, agent = _make_admin_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/api/v1/analytics/spend")
        assert resp.status_code == 200
        body = resp.json()
        assert "forecast" in body
        assert body["forecast"]["current_spend_usd"] == pytest.approx(5.67)


# ── M.2 — _compute_forecast pure-math suite ──────────────────────────


class TestForecastMath:
    """Drive the forecast helper directly so we don't depend on wall-clock time."""

    def _import(self):
        from proxy.routes.admin import _compute_forecast
        return _compute_forecast

    def test_typical_midday_burn(self):
        f = self._import()(spent=12.0, daily_limit=50.0, elapsed_hours=12.0)
        assert f["burn_rate_usd_per_hour"] == pytest.approx(1.0)
        assert f["projected_daily_total_usd"] == pytest.approx(24.0)
        assert f["headroom_usd"] == pytest.approx(38.0)
        assert f["time_to_limit_hours"] == pytest.approx(38.0)

    def test_below_min_elapsed_returns_nulls_for_rate_fields(self):
        """Just past midnight — one cheap call shouldn't extrapolate to wild
        projections."""
        f = self._import()(spent=0.05, daily_limit=50.0, elapsed_hours=0.01)
        assert f["burn_rate_usd_per_hour"] is None
        assert f["projected_daily_total_usd"] is None
        assert f["time_to_limit_hours"] is None
        # But the raw inputs are still surfaced.
        assert f["current_spend_usd"] == pytest.approx(0.05)
        assert f["daily_limit_usd"] == pytest.approx(50.0)
        assert f["elapsed_hours"] == pytest.approx(0.01)

    def test_already_over_limit_pins_time_to_zero(self):
        """Headroom <= 0 surfaces a hard zero so the UI can render 'over limit'
        without div-by-zero."""
        f = self._import()(spent=60.0, daily_limit=50.0, elapsed_hours=10.0)
        assert f["headroom_usd"] == pytest.approx(-10.0)
        assert f["time_to_limit_hours"] == 0.0

    def test_zero_burn_rate_leaves_time_null(self):
        """Zero spend → no rate → indefinite, not infinity."""
        f = self._import()(spent=0.0, daily_limit=50.0, elapsed_hours=4.0)
        assert f["burn_rate_usd_per_hour"] == 0.0
        assert f["headroom_usd"] == pytest.approx(50.0)
        assert f["time_to_limit_hours"] is None  # would be ∞ — express as null

    def test_no_daily_limit_skips_headroom(self):
        """No budget configured → forecast still computes burn rate but limit
        fields stay null."""
        f = self._import()(spent=5.0, daily_limit=0.0, elapsed_hours=5.0)
        assert f["daily_limit_usd"] is None
        assert f["headroom_usd"] is None
        assert f["time_to_limit_hours"] is None
        assert f["burn_rate_usd_per_hour"] == pytest.approx(1.0)
        assert f["projected_daily_total_usd"] == pytest.approx(24.0)

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

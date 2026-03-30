"""
Coverage tests for HTTP routes via httpx AsyncClient.

Tests the actual FastAPI route handlers against a mock agent,
exercising the full middleware stack without network I/O.

Targets: telemetry.py, models.py, completions.py, embeddings.py,
         admin.py (partial), plugins.py (partial), registry.py (partial).
"""

import time
import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock
from httpx import AsyncClient, ASGITransport
from fastapi import FastAPI

from tests.conftest import InMemoryRepository, minimal_config


# ── Fixtures ──────────────────────────────────────────────────

def _make_mock_agent():
    """Create a minimal mock agent with enough state for route handlers."""
    agent = MagicMock()
    agent.config = minimal_config(auth_enabled=False)
    agent.config["endpoints"] = {
        "openai": {"provider": "openai", "models": ["gpt-4o", "gpt-4o-mini"], "api_key_env": "OPENAI_API_KEY"},
        "anthropic": {"provider": "anthropic", "models": ["claude-sonnet-4-20250514"], "api_key_env": "ANTHROPIC_API_KEY"},
        "ollama": {"provider": "ollama", "models": ["llama3.3"], "auth_type": "none"},
    }
    agent.config["model_aliases"] = {"fast": "gpt-4o-mini"}

    store = InMemoryRepository()
    agent.store = store
    agent._session = None
    agent.total_cost_today = 1.23
    agent._start_time = time.time() - 120  # 2 min uptime
    agent._version = "1.8.0"

    # Circuit manager mock
    breaker = MagicMock()
    breaker.can_execute = AsyncMock(return_value=True)
    breaker.report_success = AsyncMock()
    breaker.report_failure = AsyncMock()
    agent.circuit_manager = MagicMock()
    agent.circuit_manager.get_breaker.return_value = breaker

    # Plugin manager mock
    agent.plugin_manager = MagicMock()
    agent.plugin_manager._plugin_instances = {}
    agent.plugin_manager._ring_traces = []
    agent.plugin_manager._ring_traces_index = {}

    # Log queue
    agent.log_queue = asyncio.Queue(maxsize=100)

    return agent


def _make_app_with_routes(*route_factories):
    """Build a minimal FastAPI app with specific route factories."""
    agent = _make_mock_agent()
    app = FastAPI()
    for factory in route_factories:
        app.include_router(factory(agent))
    return app, agent


# ── Telemetry Routes ──────────────────────────────────────────

class TestTelemetryRoutes:

    @pytest.mark.asyncio
    async def test_health_endpoint(self):
        from proxy.routes.telemetry import create_router
        app, agent = _make_app_with_routes(create_router)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/health")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["version"] == "1.8.0"
        assert data["pool_size"] == 0
        assert data["pool_healthy"] == 0
        assert "uptime_seconds" in data
        assert "budget_today_usd" in data

    @pytest.mark.asyncio
    async def test_metrics_endpoint(self):
        from proxy.routes.telemetry import create_router
        app, agent = _make_app_with_routes(create_router)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/metrics")

        assert resp.status_code == 200


# ── Models Routes ─────────────────────────────────────────────

class TestModelsRoutes:

    @pytest.mark.asyncio
    async def test_list_models(self):
        from proxy.routes.models import create_router
        app, agent = _make_app_with_routes(create_router)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/v1/models")

        assert resp.status_code == 200
        data = resp.json()
        assert data["object"] == "list"
        model_ids = [m["id"] for m in data["data"]]
        assert "gpt-4o" in model_ids
        assert "claude-sonnet-4-20250514" in model_ids
        assert "llama3.3" in model_ids

    @pytest.mark.asyncio
    async def test_list_models_deduplicates(self):
        from proxy.routes.models import create_router
        app, agent = _make_app_with_routes(create_router)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/v1/models")

        data = resp.json()
        ids = [m["id"] for m in data["data"]]
        assert len(ids) == len(set(ids)), "Duplicate model IDs in response"

    @pytest.mark.asyncio
    async def test_list_models_sorted(self):
        from proxy.routes.models import create_router
        app, agent = _make_app_with_routes(create_router)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/v1/models")

        data = resp.json()
        owners = [m["owned_by"] for m in data["data"]]
        # Should be sorted by provider then model
        assert owners == sorted(owners)

    @pytest.mark.asyncio
    async def test_get_single_model_known(self):
        from proxy.routes.models import create_router
        app, agent = _make_app_with_routes(create_router)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/v1/models/gpt-4o")

        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "gpt-4o"
        assert data["owned_by"] == "openai"

    @pytest.mark.asyncio
    async def test_get_single_model_unknown_fallback(self):
        from proxy.routes.models import create_router
        app, agent = _make_app_with_routes(create_router)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/v1/models/nonexistent-model")

        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "nonexistent-model"
        assert data["object"] == "model"


# ── Telemetry: sanitize_log ──────────────────────────────────

class TestTelemetrySanitization:

    def test_sanitize_strips_ansi(self):
        from proxy.routes.telemetry import _sanitize_log
        log = {"message": "\x1b[31mRED TEXT\x1b[0m normal"}
        result = _sanitize_log(log)
        assert "\x1b" not in result["message"]
        assert "RED TEXT" in result["message"]

    def test_sanitize_strips_control_chars(self):
        from proxy.routes.telemetry import _sanitize_log
        log = {"message": "hello\x00world\x07bell"}
        result = _sanitize_log(log)
        assert "\x00" not in result["message"]
        assert "\x07" not in result["message"]

    def test_sanitize_nested_dict(self):
        from proxy.routes.telemetry import _sanitize_log
        log = {"metadata": {"inner": "\x1b[31mcolored\x1b[0m"}}
        result = _sanitize_log(log)
        assert "\x1b" not in result["metadata"]["inner"]

    def test_sanitize_preserves_non_string(self):
        from proxy.routes.telemetry import _sanitize_log
        log = {"count": 42, "flag": True}
        result = _sanitize_log(log)
        assert result["count"] == 42
        assert result["flag"] is True

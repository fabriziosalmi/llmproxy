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
        "openai": {
            "provider": "openai",
            "models": ["gpt-4o", "gpt-4o-mini"],
            "api_key_env": "OPENAI_API_KEY",
        },
        "anthropic": {
            "provider": "anthropic",
            "models": ["claude-sonnet-4-20250514"],
            "api_key_env": "ANTHROPIC_API_KEY",
        },
        "ollama": {"provider": "ollama", "models": ["llama3.3"], "auth_type": "none"},
    }
    agent.config["model_aliases"] = {"fast": "gpt-4o-mini"}

    store = InMemoryRepository()
    agent.store = store
    # Session: real-enough mock so M.3 /health classifies session as "ok".
    session = MagicMock()
    session.closed = False
    agent._session = session
    agent.total_cost_today = 1.23
    agent._start_time = time.time() - 120  # 2 min uptime
    agent._version = "1.8.0"

    # Circuit manager mock — get_all_states returns a dict so /health can
    # iterate it without exploding on MagicMock magic.
    breaker = MagicMock()
    breaker.can_execute = AsyncMock(return_value=True)
    breaker.report_success = AsyncMock()
    breaker.report_failure = AsyncMock()
    agent.circuit_manager = MagicMock()
    agent.circuit_manager.get_breaker = AsyncMock(return_value=breaker)
    agent.circuit_manager.get_all_states = MagicMock(return_value={})

    # Plugin manager mock — rings is a real dict so /health's iteration works.
    agent.plugin_manager = MagicMock()
    agent.plugin_manager._plugin_instances = {}
    agent.plugin_manager._ring_traces = []
    agent.plugin_manager._ring_traces_index = {}
    agent.plugin_manager.rings = {}

    # Cache backend — enabled by default so /health classifies as "ok".
    agent.cache_backend = MagicMock()
    agent.cache_backend._enabled = True
    agent.cache_backend.stats = AsyncMock(
        return_value={"size": 0, "hits": 0, "misses": 0}
    )

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

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/health")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["version"] == "1.8.0"
        assert data["pool_size"] == 0
        assert data["pool_healthy"] == 0
        assert "uptime_seconds" in data
        assert "budget_today_usd" in data
        # M.3 — components block surfaces per-subsystem state.
        assert set(data["components"].keys()) == {
            "endpoints",
            "store",
            "cache",
            "plugins",
            "session",
            "log_queue",
        }
        assert data["components"]["session"]["status"] == "ok"
        assert data["components"]["store"]["status"] == "ok"
        assert data["components"]["cache"]["status"] == "ok"

    @pytest.mark.asyncio
    async def test_health_session_down_marks_overall_down(self):
        """Session is critical — losing it must propagate to overall status."""
        from proxy.routes.telemetry import create_router

        app, agent = _make_app_with_routes(create_router)
        agent._session = None  # aiohttp not initialized / closed

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/health")
        data = resp.json()
        assert data["status"] == "down"
        assert data["components"]["session"]["status"] == "down"
        # HTTP status stays 200 — overall health is in the body so existing
        # pollers don't break their alerting on a 503.
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_health_cache_disabled_marks_degraded(self):
        """Cache disabled is "degraded", not "down" — proxy still serves."""
        from proxy.routes.telemetry import create_router

        app, agent = _make_app_with_routes(create_router)
        agent.cache_backend._enabled = False

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/health")
        data = resp.json()
        assert data["status"] == "degraded"
        assert data["components"]["cache"]["status"] == "degraded"

    @pytest.mark.asyncio
    async def test_health_circuit_open_marks_endpoints_degraded(self):
        """One open breaker should not panic the proxy, but should be visible."""
        from proxy.routes.telemetry import create_router

        app, agent = _make_app_with_routes(create_router)
        agent.circuit_manager.get_all_states.return_value = {
            "openai": {"state": "open", "failure_count": 5, "failure_threshold": 5},
            "anthropic": {
                "state": "closed",
                "failure_count": 0,
                "failure_threshold": 5,
            },
        }

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/health")
        data = resp.json()
        assert data["status"] == "degraded"
        assert data["components"]["endpoints"]["status"] == "degraded"
        assert data["components"]["endpoints"]["circuits_open"] == 1

    @pytest.mark.asyncio
    async def test_health_log_queue_saturation_marks_degraded(self):
        """Log queue near full → DLQ overflow imminent → degraded."""
        from proxy.routes.telemetry import create_router

        app, agent = _make_app_with_routes(create_router)
        # Fill the queue to 85% (>= 0.8 threshold).
        for _ in range(85):
            agent.log_queue.put_nowait({"level": "INFO", "message": "x"})

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/health")
        data = resp.json()
        assert data["components"]["log_queue"]["status"] == "degraded"
        assert data["components"]["log_queue"]["depth"] == 85
        assert data["components"]["log_queue"]["max"] == 100
        assert data["status"] == "degraded"

    @pytest.mark.asyncio
    async def test_metrics_endpoint(self):
        from proxy.routes.telemetry import create_router

        app, agent = _make_app_with_routes(create_router)

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/metrics")

        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_client_logs_ingest_accepts_batch(self):
        from proxy.routes.telemetry import create_router

        app, agent = _make_app_with_routes(create_router)
        agent._add_log = AsyncMock()

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/v1/logs/client",
                json={
                    "session": "abc-123",
                    "records": [
                        {
                            "level": "error",
                            "message": "boom",
                            "ts": 1234567890,
                            "context": {"a": 1},
                        },
                        {"level": "warn", "message": "soft", "ts": 1234567891},
                    ],
                },
            )

        assert resp.status_code == 202
        body = resp.json()
        assert body["accepted"] == 2
        assert body["dropped"] == 0
        assert agent._add_log.await_count == 2
        # Verify metadata was attached and level normalized.
        first_call = agent._add_log.await_args_list[0]
        assert first_call.args[0].startswith("CLIENT: ")
        assert first_call.kwargs["level"] == "ERROR"
        meta = first_call.kwargs["metadata"]
        assert meta["source"] == "client"
        assert meta["session"] == "abc-123"
        # Second record was warn → normalized to WARNING.
        assert agent._add_log.await_args_list[1].kwargs["level"] == "WARNING"

    @pytest.mark.asyncio
    async def test_client_logs_drops_invalid_records(self):
        from proxy.routes.telemetry import create_router

        app, agent = _make_app_with_routes(create_router)
        agent._add_log = AsyncMock()

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/v1/logs/client",
                json={
                    "records": [
                        {"level": "info", "message": "ok"},
                        "not-a-dict",
                        {"level": "info", "message": ""},  # empty msg → dropped
                        {
                            "level": "garbage",
                            "message": "still ok, level coerced to INFO",
                        },
                    ],
                },
            )

        assert resp.status_code == 202
        body = resp.json()
        assert body["accepted"] == 2
        assert body["dropped"] == 2

    @pytest.mark.asyncio
    async def test_client_logs_rejects_oversized_batch(self):
        from proxy.routes.telemetry import create_router

        app, agent = _make_app_with_routes(create_router)

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/v1/logs/client",
                json={"records": [{"level": "info", "message": "x"}] * 101},
            )

        assert resp.status_code == 413

    @pytest.mark.asyncio
    async def test_client_logs_404_when_disabled(self):
        from proxy.routes.telemetry import create_router

        app, agent = _make_app_with_routes(create_router)
        agent.config["security"] = {"client_logs": {"enabled": False}}

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/v1/logs/client",
                json={"records": [{"level": "info", "message": "x"}]},
            )

        assert resp.status_code == 404


# ── Models Routes ─────────────────────────────────────────────


class TestModelsRoutes:
    @pytest.mark.asyncio
    async def test_list_models(self):
        from proxy.routes.models import create_router

        app, agent = _make_app_with_routes(create_router)

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
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

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/v1/models")

        data = resp.json()
        ids = [m["id"] for m in data["data"]]
        assert len(ids) == len(set(ids)), "Duplicate model IDs in response"

    @pytest.mark.asyncio
    async def test_list_models_sorted(self):
        from proxy.routes.models import create_router

        app, agent = _make_app_with_routes(create_router)

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/v1/models")

        data = resp.json()
        owners = [m["owned_by"] for m in data["data"]]
        # Should be sorted by provider then model
        assert owners == sorted(owners)

    @pytest.mark.asyncio
    async def test_get_single_model_known(self):
        from proxy.routes.models import create_router

        app, agent = _make_app_with_routes(create_router)

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/v1/models/gpt-4o")

        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "gpt-4o"
        assert data["owned_by"] == "openai"

    @pytest.mark.asyncio
    async def test_get_single_model_unknown_fallback(self):
        from proxy.routes.models import create_router

        app, agent = _make_app_with_routes(create_router)

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
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

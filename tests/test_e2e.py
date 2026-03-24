"""
E2E Integration Tests — Full HTTP request flow through the FastAPI app.

Tests the actual route handlers with a lightweight agent mock wired to
an in-memory store. No external services, no heavy deps (otel, chromadb, etc.).

Coverage:
  - Health, metrics, version, service-info, network-info
  - Proxy toggle, priority toggle, panic kill-switch
  - Feature toggle (security shields)
  - Endpoint registry CRUD (add, list, toggle, priority, delete)
  - Plugin listing, hot-swap, rollback
  - Chat completions (auth disabled, mocked upstream)
  - Chat completions (auth enabled, valid/invalid keys)
  - Budget tracking persistence
  - State persistence across requests
"""

import asyncio
import pytest
import pytest_asyncio
import httpx
from unittest.mock import AsyncMock, MagicMock
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from conftest import InMemoryRepository, minimal_config, make_openai_response


class LightweightAgent:
    """
    Minimal agent that satisfies route handler dependencies without
    importing the full RotatorAgent (and its 20+ transitive deps).
    Wires the actual route modules from proxy/routes/.
    """

    def __init__(self, store, config, auth_enabled=False):
        self.store = store
        self.config = config
        self.proxy_enabled = True
        self.priority_mode = False
        self.total_cost_today = 0.0
        self._budget_date = "2026-03-20"
        self._session = None
        self.features = {
            "language_guard": True,
            "injection_guard": True,
            "link_sanitizer": True,
        }

        # Mocked subsystems (what the routes actually touch)
        self.security = MagicMock()
        self.security.config = {}
        self.identity = MagicMock()
        self.identity.enabled = False
        self.rbac = MagicMock()
        self.rbac.check_permission = MagicMock(return_value=True)
        self.rbac.check_quota = MagicMock(return_value=True)
        self.rbac.get_permissions_for_roles = MagicMock(return_value={"proxy:use"})
        self.webhooks = MagicMock()
        self.webhooks.dispatch = AsyncMock()
        self.chatbot = MagicMock()
        self.chatbot.notify_ops = AsyncMock()
        self.chatbot.track_error = AsyncMock()
        self.exporter = None
        self.zt_manager = MagicMock()
        self.zt_manager.verify_tailscale_identity = AsyncMock(return_value={"status": "unverified"})
        self.plugin_manager = MagicMock()
        self.plugin_manager.list_plugins = MagicMock(return_value=[])
        self.plugin_manager.manifest_path = "plugins/manifest.yaml"
        self.plugin_manager.hot_swap = AsyncMock()
        self.plugin_manager.rollback = AsyncMock()
        self.plugin_manager.install_plugin = AsyncMock()
        self.plugin_manager.uninstall_plugin = AsyncMock(return_value=True)

        # Strong references for background tasks (mirrors RotatorAgent._spawn_task)
        self._background_tasks: set[asyncio.Task] = set()

        # Queues
        self.log_queue = asyncio.Queue(maxsize=100)
        self.telemetry_queue = asyncio.Queue(maxsize=100)
        self._pending_writes: asyncio.Queue = asyncio.Queue(maxsize=500)
        self._budget_lock = asyncio.Lock()

        # proxy_request mock (for chat tests)
        self.proxy_request = AsyncMock(
            return_value=JSONResponse(content=make_openai_response(), status_code=200)
        )

        # Build FastAPI app with real route modules
        self.app = FastAPI(title="LLMPROXY-TEST")
        self.app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_methods=["*"],
            allow_headers=["*"],
        )

        # Request deduplication
        from core.deduplicator import RequestDeduplicator
        self.deduplicator = RequestDeduplicator()

        from proxy.routes import (
            admin_router, registry_router, identity_router,
            plugins_router, telemetry_router, chat_router,
            models_router, embeddings_router, completions_router,
        )
        self.app.include_router(chat_router(self))
        self.app.include_router(completions_router(self))
        self.app.include_router(embeddings_router(self))
        self.app.include_router(models_router(self))
        self.app.include_router(admin_router(self))
        self.app.include_router(registry_router(self))
        self.app.include_router(identity_router(self))
        self.app.include_router(plugins_router(self))
        self.app.include_router(telemetry_router(self))

    def _spawn_task(self, coro) -> asyncio.Task:
        """Create a background task with a strong reference to prevent GC."""
        task = asyncio.create_task(coro)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return task

    def enqueue_write(self, key: str, value):
        """Test-mode: direct write via create_task (no batching needed in tests)."""
        self._spawn_task(self.store.set_state(key, value))

    async def flush_budget_now(self):
        """No-op in tests — enqueue_write already writes directly."""
        pass

    def _get_api_keys(self):
        return []

    async def _add_log(self, message, level="INFO", metadata=None):
        """Lightweight log — mirrors RotatorAgent._add_log."""
        import time
        entry = {"timestamp": time.strftime("%H:%M:%S"), "level": level, "message": message}
        if self.log_queue.full():
            self.log_queue.get_nowait()
        await self.log_queue.put(entry)


# ── Fixtures ──

@pytest.fixture
def store():
    return InMemoryRepository()


@pytest.fixture
def agent(store):
    return LightweightAgent(store, minimal_config())


@pytest.fixture
def agent_with_auth(store):
    return LightweightAgent(store, minimal_config(auth_enabled=True), auth_enabled=True)


@pytest_asyncio.fixture
async def client(agent):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=agent.app),
        base_url="http://test",
    ) as c:
        yield c


@pytest_asyncio.fixture
async def auth_client(agent_with_auth):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=agent_with_auth.app),
        base_url="http://test",
    ) as c:
        yield c


# ── Health & Info Routes ──

@pytest.mark.asyncio
async def test_health_endpoint(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "pool_size" in data


@pytest.mark.asyncio
async def test_metrics_endpoint(client):
    resp = await client.get("/metrics")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_version_endpoint(client):
    resp = await client.get("/api/v1/version")
    assert resp.status_code == 200
    assert "version" in resp.json()


@pytest.mark.asyncio
async def test_service_info_endpoint(client):
    resp = await client.get("/api/v1/service-info")
    assert resp.status_code == 200
    data = resp.json()
    assert "port" in data
    assert "url" in data


@pytest.mark.asyncio
async def test_network_info_endpoint(client):
    resp = await client.get("/api/v1/network/info")
    assert resp.status_code == 200
    data = resp.json()
    assert "host" in data
    assert "tailscale_active" in data


# ── Proxy Admin Routes ──

@pytest.mark.asyncio
async def test_proxy_status(client):
    resp = await client.get("/api/v1/proxy/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["enabled"] is True
    assert data["priority_mode"] is False


@pytest.mark.asyncio
async def test_proxy_toggle_off_and_on(client, agent):
    # Turn off
    resp = await client.post("/api/v1/proxy/toggle", json={"enabled": False})
    assert resp.status_code == 200
    assert resp.json()["enabled"] is False
    assert agent.proxy_enabled is False

    # Turn back on
    resp = await client.post("/api/v1/proxy/toggle", json={"enabled": True})
    assert resp.status_code == 200
    assert resp.json()["enabled"] is True
    assert agent.proxy_enabled is True


@pytest.mark.asyncio
async def test_priority_toggle(client, agent):
    resp = await client.post("/api/v1/proxy/priority/toggle", json={"enabled": True})
    assert resp.status_code == 200
    assert resp.json()["enabled"] is True
    assert agent.priority_mode is True


@pytest.mark.asyncio
async def test_panic_killswitch(client, agent):
    resp = await client.post("/api/v1/panic")
    assert resp.status_code == 200
    assert resp.json()["status"] == "HALTED"
    assert agent.proxy_enabled is False


# ── Feature Toggle Routes ──

@pytest.mark.asyncio
async def test_features_list(client):
    resp = await client.get("/api/v1/features")
    assert resp.status_code == 200
    data = resp.json()
    assert "language_guard" in data
    assert "injection_guard" in data


@pytest.mark.asyncio
async def test_feature_toggle(client, agent):
    resp = await client.post("/api/v1/features/toggle", json={"name": "injection_guard", "enabled": False})
    assert resp.status_code == 200
    assert resp.json()["enabled"] is False
    assert agent.features["injection_guard"] is False

    # Toggle back
    resp = await client.post("/api/v1/features/toggle", json={"name": "injection_guard", "enabled": True})
    assert resp.json()["enabled"] is True


@pytest.mark.asyncio
async def test_feature_toggle_unknown(client):
    resp = await client.post("/api/v1/features/toggle", json={"name": "nonexistent"})
    assert resp.status_code == 400


# ── Registry Routes ──

@pytest.mark.asyncio
async def test_registry_empty(client):
    resp = await client.get("/api/v1/registry")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_registry_with_endpoint(client, store):
    from models import LLMEndpoint, EndpointStatus
    ep = LLMEndpoint(
        id="ep-1",
        url="http://localhost:11434/v1",
        status=EndpointStatus.VERIFIED,
        metadata={"provider_type": "Ollama", "priority": 5},
        latency_ms=42.0,
        success_rate=0.99,
    )
    await store.add_endpoint(ep)

    resp = await client.get("/api/v1/registry")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["id"] == "ep-1"
    assert data[0]["status"] == "Live"
    assert data[0]["priority"] == 5
    assert data[0]["type"] == "Ollama"


@pytest.mark.asyncio
async def test_registry_toggle_endpoint(client, store):
    from models import LLMEndpoint, EndpointStatus
    ep = LLMEndpoint(id="ep-2", url="http://example.com/v1", status=EndpointStatus.VERIFIED, metadata={})
    await store.add_endpoint(ep)

    resp = await client.post("/api/v1/registry/ep-2/toggle")
    assert resp.status_code == 200
    assert resp.json()["status"] == 1  # VERIFIED (3) → IGNORED (1)


@pytest.mark.asyncio
async def test_registry_set_priority(client, store):
    from models import LLMEndpoint, EndpointStatus
    ep = LLMEndpoint(id="ep-3", url="http://example.com/v2", status=EndpointStatus.VERIFIED, metadata={})
    await store.add_endpoint(ep)

    resp = await client.post("/api/v1/registry/ep-3/priority", json={"priority": 10})
    assert resp.status_code == 200
    assert resp.json()["priority"] == 10


@pytest.mark.asyncio
async def test_registry_delete_endpoint(client, store):
    from models import LLMEndpoint, EndpointStatus
    ep = LLMEndpoint(id="ep-4", url="http://example.com/v3", status=EndpointStatus.VERIFIED, metadata={})
    await store.add_endpoint(ep)

    resp = await client.delete("/api/v1/registry/ep-4")
    assert resp.status_code == 200

    # Verify deleted
    resp = await client.get("/api/v1/registry")
    assert len(resp.json()) == 0


@pytest.mark.asyncio
async def test_registry_toggle_nonexistent(client):
    resp = await client.post("/api/v1/registry/does-not-exist/toggle")
    assert resp.status_code == 404


# ── Plugin Routes ──

@pytest.mark.asyncio
async def test_plugins_list(client):
    resp = await client.get("/api/v1/plugins")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_plugins_hot_swap(client):
    resp = await client.post("/api/v1/plugins/hot-swap")
    assert resp.status_code == 200
    assert resp.json()["status"] in ("success", "rolled_back")


@pytest.mark.asyncio
async def test_plugins_rollback(client):
    resp = await client.post("/api/v1/plugins/rollback")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_plugins_install_missing_fields(client):
    resp = await client.post("/api/v1/plugins/install", json={"name": "test"})
    assert resp.status_code == 400


# ── Identity Routes ──

@pytest.mark.asyncio
async def test_identity_config(client):
    resp = await client.get("/api/v1/identity/config")
    assert resp.status_code == 200
    # Identity disabled in test config
    assert resp.json()["enabled"] is False


@pytest.mark.asyncio
async def test_identity_me_unauthenticated(client):
    resp = await client.get("/api/v1/identity/me")
    assert resp.status_code == 200
    assert resp.json()["authenticated"] is False


# ── Chat Completions (Auth Disabled) ──

@pytest.mark.asyncio
async def test_chat_completions_no_auth(client, agent):
    """With auth disabled, chat should work without API key."""
    mock_response = JSONResponse(
        content=make_openai_response("Test response"),
        status_code=200,
    )
    agent.proxy_request = AsyncMock(return_value=mock_response)

    resp = await client.post("/v1/chat/completions", json={
        "model": "gpt-4",
        "messages": [{"role": "user", "content": "Hello"}],
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["choices"][0]["message"]["content"] == "Test response"
    agent.proxy_request.assert_called_once()


@pytest.mark.asyncio
async def test_chat_completions_proxy_disabled(client, agent):
    """When proxy is stopped, requests should be rejected."""
    agent.proxy_enabled = False
    resp = await client.post("/v1/chat/completions", json={
        "model": "gpt-4",
        "messages": [{"role": "user", "content": "Hello"}],
    })
    assert resp.status_code == 503
    agent.proxy_enabled = True  # Reset


# ── Chat Completions (Auth Enabled) ──

@pytest.mark.asyncio
async def test_chat_auth_missing_key(auth_client):
    """Requests without API key should be rejected."""
    resp = await auth_client.post("/v1/chat/completions", json={
        "model": "gpt-4",
        "messages": [{"role": "user", "content": "Hello"}],
    })
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_chat_auth_invalid_key(auth_client, agent_with_auth):
    """Requests with wrong API key should be rejected."""
    agent_with_auth._get_api_keys = lambda: ["sk-valid-key"]
    resp = await auth_client.post(
        "/v1/chat/completions",
        json={"model": "gpt-4", "messages": [{"role": "user", "content": "Hello"}]},
        headers={"Authorization": "Bearer sk-wrong-key"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_chat_auth_valid_key(auth_client, agent_with_auth):
    """Requests with valid API key should succeed."""
    agent_with_auth._get_api_keys = lambda: ["sk-valid-key"]
    mock_response = JSONResponse(content=make_openai_response(), status_code=200)
    agent_with_auth.proxy_request = AsyncMock(return_value=mock_response)

    resp = await auth_client.post(
        "/v1/chat/completions",
        json={"model": "gpt-4", "messages": [{"role": "user", "content": "Hello"}]},
        headers={"Authorization": "Bearer sk-valid-key"},
    )
    assert resp.status_code == 200


# ── Budget Persistence ──

@pytest.mark.asyncio
async def test_budget_persisted_after_request(client, agent, store):
    """J.5: total_cost_today should be persisted to store after each request."""
    mock_response = JSONResponse(content=make_openai_response(), status_code=200)
    agent.proxy_request = AsyncMock(return_value=mock_response)

    await client.post("/v1/chat/completions", json={
        "model": "gpt-4",
        "messages": [{"role": "user", "content": "Hello"}],
    })

    # Give async tasks time to complete
    await asyncio.sleep(0.1)

    saved = await store.get_state("budget:daily_total")
    assert saved is not None
    assert saved > 0


# ── State Persistence ──

@pytest.mark.asyncio
async def test_proxy_toggle_persists_state(client, store):
    await client.post("/api/v1/proxy/toggle", json={"enabled": False})
    saved = await store.get_state("proxy_enabled")
    assert saved is False


@pytest.mark.asyncio
async def test_feature_toggle_persists_state(client, store):
    await client.post("/api/v1/features/toggle", json={"name": "language_guard", "enabled": False})
    saved = await store.get_state("feature_language_guard")
    assert saved is False


@pytest.mark.asyncio
async def test_priority_toggle_persists_state(client, store):
    await client.post("/api/v1/proxy/priority/toggle", json={"enabled": True})
    saved = await store.get_state("priority_mode")
    assert saved is True


# ── GET /v1/models ──


@pytest.mark.asyncio
async def test_models_empty_config(client):
    """No endpoints configured → empty model list."""
    resp = await client.get("/v1/models")
    assert resp.status_code == 200
    data = resp.json()
    assert data["object"] == "list"
    assert isinstance(data["data"], list)


@pytest_asyncio.fixture
async def client_with_models():
    """Client with endpoints configured in config."""
    store = InMemoryRepository()
    config = minimal_config()
    config["endpoints"] = {
        "openai": {"provider": "openai", "models": ["gpt-4o", "gpt-4o-mini"]},
        "anthropic": {"provider": "anthropic", "models": ["claude-sonnet-4-20250514"]},
        "ollama": {"provider": "ollama", "models": ["llama3.3"]},
    }
    agent = LightweightAgent(store, config)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=agent.app), base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_models_list(client_with_models):
    resp = await client_with_models.get("/v1/models")
    assert resp.status_code == 200
    data = resp.json()
    assert data["object"] == "list"
    model_ids = [m["id"] for m in data["data"]]
    assert "gpt-4o" in model_ids
    assert "gpt-4o-mini" in model_ids
    assert "claude-sonnet-4-20250514" in model_ids
    assert "llama3.3" in model_ids


@pytest.mark.asyncio
async def test_models_owned_by(client_with_models):
    resp = await client_with_models.get("/v1/models")
    data = resp.json()
    by_id = {m["id"]: m for m in data["data"]}
    assert by_id["gpt-4o"]["owned_by"] == "openai"
    assert by_id["claude-sonnet-4-20250514"]["owned_by"] == "anthropic"
    assert by_id["llama3.3"]["owned_by"] == "ollama"


@pytest.mark.asyncio
async def test_models_object_format(client_with_models):
    resp = await client_with_models.get("/v1/models")
    for model in resp.json()["data"]:
        assert model["object"] == "model"
        assert "id" in model
        assert "created" in model
        assert "owned_by" in model


@pytest.mark.asyncio
async def test_model_get_single(client_with_models):
    resp = await client_with_models.get("/v1/models/gpt-4o")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == "gpt-4o"
    assert data["object"] == "model"
    assert data["owned_by"] == "openai"


@pytest.mark.asyncio
async def test_model_get_unknown(client_with_models):
    resp = await client_with_models.get("/v1/models/some-unknown-model")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == "some-unknown-model"
    assert data["object"] == "model"


@pytest.mark.asyncio
async def test_models_no_duplicates(client_with_models):
    resp = await client_with_models.get("/v1/models")
    model_ids = [m["id"] for m in resp.json()["data"]]
    assert len(model_ids) == len(set(model_ids))

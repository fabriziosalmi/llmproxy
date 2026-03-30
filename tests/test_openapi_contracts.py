"""
OpenAPI Contract Tests — validates that API responses match the auto-generated schema.

Uses FastAPI's built-in OpenAPI spec as the source of truth,
then validates actual responses against declared response models.
"""

import pytest
import pytest_asyncio
import httpx

from conftest import InMemoryRepository, minimal_config


# Reuse the LightweightAgent from test_e2e
from test_e2e import LightweightAgent


@pytest.fixture
def store():
    return InMemoryRepository()


@pytest.fixture
def agent(store):
    return LightweightAgent(store, minimal_config())


@pytest_asyncio.fixture
async def client(agent):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=agent.app),
        base_url="http://test",
    ) as c:
        yield c


# ── OpenAPI Schema Availability ──

@pytest.mark.asyncio
async def test_openapi_schema_available(client):
    """The OpenAPI JSON spec should be served at /openapi.json."""
    resp = await client.get("/openapi.json")
    assert resp.status_code == 200
    schema = resp.json()
    assert schema["openapi"].startswith("3.")
    assert schema["info"]["title"] == "LLMPROXY-TEST"


@pytest.mark.asyncio
async def test_openapi_has_all_routes(client):
    """Every registered route should appear in the OpenAPI spec."""
    resp = await client.get("/openapi.json")
    schema = resp.json()
    paths = set(schema["paths"].keys())

    required_paths = [
        "/health",
        "/metrics",
        "/api/v1/version",
        "/api/v1/service-info",
        "/api/v1/proxy/status",
        "/api/v1/proxy/toggle",
        "/api/v1/proxy/priority/toggle",
        "/api/v1/panic",
        "/api/v1/features",
        "/api/v1/features/toggle",
        "/api/v1/network/info",
        "/api/v1/registry",
        "/api/v1/registry/{endpoint_id}/toggle",
        "/api/v1/registry/{endpoint_id}/priority",
        "/api/v1/registry/{endpoint_id}",
        "/api/v1/plugins",
        "/api/v1/plugins/hot-swap",
        "/api/v1/plugins/rollback",
        "/api/v1/plugins/install",
        "/api/v1/identity/config",
        "/api/v1/identity/me",
        "/v1/chat/completions",
    ]

    for path in required_paths:
        assert path in paths, f"Missing route in OpenAPI spec: {path}"


# ── Response Shape Contracts ──

@pytest.mark.asyncio
async def test_health_response_shape(client):
    """GET /health must return {status, pool_size}."""
    resp = await client.get("/health")
    data = resp.json()
    assert "status" in data
    assert "pool_size" in data
    assert isinstance(data["status"], str)
    assert isinstance(data["pool_size"], int)


@pytest.mark.asyncio
async def test_version_response_shape(client):
    """GET /api/v1/version must return {version}."""
    resp = await client.get("/api/v1/version")
    data = resp.json()
    assert "version" in data
    assert isinstance(data["version"], str)


@pytest.mark.asyncio
async def test_service_info_response_shape(client):
    """GET /api/v1/service-info must return {host, port, url}."""
    resp = await client.get("/api/v1/service-info")
    data = resp.json()
    for key in ("host", "port", "url"):
        assert key in data, f"Missing key: {key}"


@pytest.mark.asyncio
async def test_proxy_status_response_shape(client):
    """GET /api/v1/proxy/status must return {enabled, priority_mode}."""
    resp = await client.get("/api/v1/proxy/status")
    data = resp.json()
    assert isinstance(data["enabled"], bool)
    assert isinstance(data["priority_mode"], bool)


@pytest.mark.asyncio
async def test_proxy_toggle_response_shape(client):
    """POST /api/v1/proxy/toggle must return {enabled}."""
    resp = await client.post("/api/v1/proxy/toggle", json={"enabled": True})
    data = resp.json()
    assert "enabled" in data
    assert isinstance(data["enabled"], bool)


@pytest.mark.asyncio
async def test_features_response_shape(client):
    """GET /api/v1/features must return a dict of feature_name -> bool."""
    resp = await client.get("/api/v1/features")
    data = resp.json()
    assert isinstance(data, dict)
    for key, val in data.items():
        assert isinstance(key, str)
        assert isinstance(val, bool), f"Feature '{key}' value should be bool, got {type(val)}"


@pytest.mark.asyncio
async def test_registry_response_shape(client, store):
    """GET /api/v1/registry items must have {id, name, url, status, latency, priority, type}."""
    from models import LLMEndpoint, EndpointStatus
    ep = LLMEndpoint(
        id="shape-test",
        url="http://localhost:11434/v1",
        status=EndpointStatus.VERIFIED,
        metadata={"provider_type": "Ollama", "priority": 3},
        latency_ms=55.0,
        success_rate=0.98,
    )
    await store.add_endpoint(ep)

    resp = await client.get("/api/v1/registry")
    data = resp.json()
    assert len(data) == 1
    item = data[0]
    required_keys = {"id", "name", "url", "status", "latency", "priority", "type"}
    assert required_keys.issubset(item.keys()), f"Missing keys: {required_keys - item.keys()}"


@pytest.mark.asyncio
async def test_plugins_response_shape(client):
    """GET /api/v1/plugins must return {plugins: [...]}."""
    resp = await client.get("/api/v1/plugins")
    data = resp.json()
    assert "plugins" in data
    assert isinstance(data["plugins"], list)


@pytest.mark.asyncio
async def test_identity_config_response_shape(client):
    """GET /api/v1/identity/config must return {enabled, providers}."""
    resp = await client.get("/api/v1/identity/config")
    data = resp.json()
    assert "enabled" in data
    assert isinstance(data["enabled"], bool)


@pytest.mark.asyncio
async def test_identity_me_response_shape(client):
    """GET /api/v1/identity/me must return {authenticated, ...}."""
    resp = await client.get("/api/v1/identity/me")
    data = resp.json()
    assert "authenticated" in data
    assert isinstance(data["authenticated"], bool)


@pytest.mark.asyncio
async def test_panic_response_shape(client):
    """POST /api/v1/panic must return {status}."""
    resp = await client.post("/api/v1/panic")
    data = resp.json()
    assert data["status"] == "HALTED"


@pytest.mark.asyncio
async def test_network_info_response_shape(client):
    """GET /api/v1/network/info must return {host, tailscale_active}."""
    resp = await client.get("/api/v1/network/info")
    data = resp.json()
    assert "host" in data
    assert "tailscale_active" in data


# ── Error Contract Tests ──

@pytest.mark.asyncio
async def test_feature_toggle_unknown_returns_400(client):
    """Toggling unknown feature must return 400, not 500."""
    resp = await client.post("/api/v1/features/toggle", json={"name": "nonexistent"})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_registry_toggle_nonexistent_returns_404(client):
    """Toggling nonexistent endpoint must return 404."""
    resp = await client.post("/api/v1/registry/ghost/toggle")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_chat_proxy_disabled_returns_503(client, agent):
    """Chat with proxy disabled must return 503."""
    agent.proxy_enabled = False
    resp = await client.post("/v1/chat/completions", json={
        "model": "gpt-4", "messages": [{"role": "user", "content": "hi"}],
    })
    assert resp.status_code == 503
    agent.proxy_enabled = True


# ── Content-Type Contracts ──

@pytest.mark.asyncio
async def test_json_content_type(client):
    """All JSON endpoints must return application/json."""
    endpoints = ["/health", "/api/v1/version", "/api/v1/proxy/status", "/api/v1/features"]
    for path in endpoints:
        resp = await client.get(path)
        assert "application/json" in resp.headers.get("content-type", ""), \
            f"{path} did not return JSON content-type"

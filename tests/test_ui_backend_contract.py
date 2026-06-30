"""
Contract tests for the backend endpoints consumed by UI services.

These tests exist because the UI (ui/services/*.js) reads specific fields
off specific endpoints. If the backend drops / renames / restructures any
of those fields, the UI silently breaks. This suite is the tripwire: it
pins the field names the UI depends on so a casual refactor can't ship
without the UI author seeing the CI diff.

Targeted services → endpoints:
  ui/services/explain.js       → /api/v1/guards/status (firewall + features + circuit_breakers)
  ui/services/drilldown.js     → /api/v1/audit, /api/v1/registry, /v1/models, /api/v1/plugins
  ui/services/timerange.js     → /api/v1/audit (items[].ts is used client-side)
"""

import time
from unittest.mock import MagicMock, AsyncMock

import pytest
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport

from tests.conftest import InMemoryRepository, minimal_config


# ── Fixture: mock agent + app with all relevant route modules ──────────────


def _mock_agent():
    agent = MagicMock()
    agent.config = minimal_config(auth_enabled=False)
    agent.config["endpoints"] = {
        "openai": {
            "provider": "openai",
            "base_url": "https://api.openai.com/v1",
            "models": ["gpt-4o", "gpt-4o-mini"],
            "api_key_env": "OPENAI_API_KEY",
        },
        "ollama": {
            "provider": "ollama",
            "base_url": "http://localhost:11434",
            "models": ["llama3.3"],
            "auth_type": "none",
        },
    }
    agent.config["model_aliases"] = {"fast": "gpt-4o-mini"}
    agent.config["rate_limiting"] = {"enabled": True, "requests_per_minute": 60}
    agent.config["budget"] = {"daily_limit": 50.0, "soft_limit": 40.0}

    agent.store = InMemoryRepository()
    agent.features = {
        "injection_guard": True,
        "language_guard": True,
        "link_sanitizer": True,
    }
    agent.total_cost_today = 0.0
    agent._budget_date = time.strftime("%Y-%m-%d")
    agent.firewall_enabled = True
    agent.firewall_disabled_reason = None

    breaker_state = {"state": "closed", "failure_count": 0, "failure_threshold": 5}
    agent.circuit_manager = MagicMock()
    agent.circuit_manager.get_all_states = AsyncMock(return_value={"openai": breaker_state})
    return agent


def _make_app():
    agent = _mock_agent()
    app = FastAPI()
    from proxy.routes.admin import create_router as admin
    from proxy.routes.registry import create_router as registry
    from proxy.routes.models import create_router as models
    from proxy.routes.plugins import create_router as plugins

    app.include_router(admin(agent))
    app.include_router(registry(agent))
    app.include_router(models(agent))
    app.include_router(plugins(agent))
    return app, agent


@pytest.fixture
def app_agent():
    return _make_app()


# ── /api/v1/guards/status — explain.js depends on: firewall.enabled,
#                            firewall.disabled_reason, firewall.total_scanned,
#                            firewall.total_blocked, firewall.block_by_signature,
#                            firewall.signatures_count, features, circuit_breakers
# ───────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_guards_status_contract(app_agent):
    """explain.js reads firewall.* + features + circuit_breakers — pin the shape."""
    app, _agent = app_agent
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        r = await c.get("/api/v1/guards/status")
    assert r.status_code == 200
    data = r.json()

    fw = data.get("firewall")
    assert fw is not None, "Missing firewall key — explain drawer for WAF will break"
    for field in (
        "enabled",
        "disabled_reason",
        "total_scanned",
        "total_blocked",
        "block_by_signature",
        "signatures_count",
    ):
        assert field in fw, f"firewall.{field} missing"

    assert "features" in data, "explain.js reads data.features for guard cards"
    assert isinstance(data["features"], dict)

    assert "circuit_breakers" in data, (
        "explain.js reads circuit_breakers[<id>] for circuit kind"
    )
    # Each breaker must expose the fields the drawer shows (state, failure_count, failure_threshold).
    for ep_id, cb in data["circuit_breakers"].items():
        assert "state" in cb
        assert "failure_count" in cb
        assert "failure_threshold" in cb

    # Security dashboard KPI cards (ui/src/views/security) read these exact paths:
    #   data.security_shield.threat_ledger.tracked_ips  → "Tracked IPs" card
    #   data.response_signing.enabled                   → "Response Signing" card
    # Missing them silently renders "—" / "OFF" regardless of real state.
    assert "security_shield" in data, "Security dashboard reads data.security_shield.threat_ledger"
    assert "threat_ledger" in data["security_shield"]
    assert "response_signing" in data, "Security dashboard reads data.response_signing.enabled"
    assert "enabled" in data["response_signing"]


# ── /api/v1/registry — drilldown.js endpoint kind + palette jump-to >ep ────


@pytest.mark.asyncio
async def test_registry_list_contract(app_agent):
    """Endpoint rows must include id, url, status, latency, priority, type,
    circuit_state, failure_count, failure_threshold."""
    app, agent = _make_app()
    # Seed so the store has something to return.
    from models import LLMEndpoint, EndpointStatus

    await agent.store.add_endpoint(
        LLMEndpoint(
            id="openai",
            url="https://api.openai.com/v1",
            status=EndpointStatus.VERIFIED,
            metadata={"provider": "openai", "models": ["gpt-4o"]},
        )
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        r = await c.get("/api/v1/registry")
    assert r.status_code == 200
    items = r.json()
    assert isinstance(items, list)
    assert len(items) >= 1
    row = items[0]
    for field in (
        "id",
        "name",
        "url",
        "status",
        "latency",
        "priority",
        "type",
        "circuit_state",
        "failure_count",
        "failure_threshold",
    ):
        assert field in row, f"registry row missing '{field}'"


# ── /v1/models — drilldown.js model kind + palette jump-to >model ──────────


@pytest.mark.asyncio
async def test_models_list_contract(app_agent):
    app, _ = app_agent
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        r = await c.get("/v1/models")
    assert r.status_code == 200
    data = r.json()
    assert data.get("object") == "list"
    assert isinstance(data.get("data"), list)
    assert data["data"], "Expected at least one model from the mock config"
    for m in data["data"]:
        # UI reads m.id (as the model identifier) and m.owned_by (provider grouping).
        assert "id" in m and m["id"], "model.id required"
        assert "owned_by" in m, (
            "model.owned_by required — drilldown model kind groups by this"
        )
        assert "object" in m
        assert m["object"] == "model"


@pytest.mark.asyncio
async def test_models_include_config_endpoints(app_agent):
    """Every endpoint's advertised model must appear in /v1/models."""
    app, _ = app_agent
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        r = await c.get("/v1/models")
    ids = {m["id"] for m in r.json().get("data", [])}
    # The mock config declares gpt-4o + gpt-4o-mini on openai, llama3.3 on ollama.
    for expected in ("gpt-4o", "gpt-4o-mini", "llama3.3"):
        assert expected in ids, (
            f"{expected} missing from /v1/models (config → UI inventory broken)"
        )


# ── /api/v1/plugins — drilldown.js plugin kind + palette jump-to >plugin ───


@pytest.mark.asyncio
async def test_plugins_list_shape(app_agent):
    """Drilldown tolerates both {plugins: [...]} and raw-array responses.
    The contract is: each entry has a stable name field (everything else is
    best-effort and rendered with em-dash fallbacks)."""
    app, agent = app_agent
    # The real plugin_manager.list_plugins() returns a list of dicts; the
    # mock defaults to an auto-MagicMock which breaks the JSON encoder, so
    # set the return value explicitly.
    agent.plugin_manager.list_plugins.return_value = [
        {
            "name": "smart_router",
            "hook": "routing",
            "description": "Smart weighted routing",
            "timeout_ms": 250,
            "fail_policy": "closed",
            "enabled": True,
            "version": "1.0.0",
        },
    ]
    import os as _os

    # The route falls back to a manifest file when list_plugins() returns
    # nothing — make sure that path isn't hit here by giving it content.
    agent.plugin_manager.manifest_path = "/nonexistent.yaml"
    assert not _os.path.exists(agent.plugin_manager.manifest_path)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        r = await c.get("/api/v1/plugins")
    assert r.status_code == 200
    data = r.json()
    # Either an array, a {plugins: [...]} envelope, or a {data: [...]} envelope.
    items = (
        data
        if isinstance(data, list)
        else (data.get("plugins") or data.get("data") or [])
    )
    assert items, f"Expected at least one plugin; got {data!r}"
    assert any(p.get("name") == "smart_router" for p in items), (
        "drilldown.js looks up plugins by p.name — the field must be present"
    )


# ── /api/v1/audit — drilldown.js request kind + timerange.js client-side filter
# ───────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_audit_shape(app_agent):
    """The audit endpoint must respond 200 with {items: [...], total: N}.
    Each item read by drilldown.js: ts, req_id, session_id, model, provider,
    status, prompt_tokens, completion_tokens, cost_usd, latency_ms, blocked,
    metadata, entry_hash, prev_hash. Fields may be null/0 but the key must exist."""
    app, agent = app_agent

    # The route calls store.query_audit(); our in-memory repository returns
    # an empty result so we check shape only — the important contract is the
    # envelope ({items: [...]} etc.) since the UI reads `data.items`.
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        r = await c.get("/api/v1/audit?limit=5")
    assert r.status_code == 200, r.text
    data = r.json()
    assert "items" in data, "audit response must have 'items' array"
    assert isinstance(data["items"], list)


# ── N.7 — /api/v1/registry/scan ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_scan_endpoint_shape(app_agent, monkeypatch):
    """Pin the AddForm scan-button contract: response has {candidates: [...], total: N}
    and each candidate has {id, provider, base_url, models}. Discovery is
    monkeypatched so the test doesn't rely on a local Ollama running."""
    app, _agent = app_agent

    async def _fake_discover(scratch, *, timeout=1.5):
        scratch["endpoints"]["ollama"] = {
            "provider": "ollama",
            "base_url": "http://127.0.0.1:11434",
            "models": ["llama3.3", "qwen2.5:7b"],
            "auth_type": "none",
            "_source": "auto-discovery",
        }
        return ["ollama"]

    import core.local_probe as lp

    monkeypatch.setattr(lp, "discover_local_endpoints", _fake_discover)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        r = await c.post("/api/v1/registry/scan")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "candidates" in body and "total" in body
    assert body["total"] == 1
    cand = body["candidates"][0]
    for field in ("id", "provider", "base_url", "models"):
        assert field in cand, f"AddForm reads candidate.{field}; missing"
    assert cand["base_url"] == "http://127.0.0.1:11434"
    assert "llama3.3" in cand["models"]


@pytest.mark.asyncio
async def test_scan_filters_already_configured(app_agent, monkeypatch):
    """If the discovered URL is already wired up in live config, drop it from
    candidates so the operator doesn't see a dup they'd have to dedupe."""
    app, agent = app_agent

    # ollama is already in the live config (see _mock_agent) at
    # http://localhost:11434. The discovery probe finds the same URL.
    async def _fake_discover(scratch, *, timeout=1.5):
        scratch["endpoints"]["ollama"] = {
            "provider": "ollama",
            "base_url": "http://localhost:11434",
            "models": ["llama3.3"],
        }
        return ["ollama"]

    import core.local_probe as lp

    monkeypatch.setattr(lp, "discover_local_endpoints", _fake_discover)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        r = await c.post("/api/v1/registry/scan")
    body = r.json()
    assert body["total"] == 0, "Already-configured URL should have been filtered"

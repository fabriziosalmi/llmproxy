"""
Regression + acceptance tests for issue #108: dangerous config/apply deltas.

An admin bearer could previously flip `server.auth.enabled` (or the firewall,
the blocklist, the payload cap) to lax in a single request. Posture-lowering
transitions now need a single-use confirm token bound to the exact proposed
text, and both rejections and confirmed applies are audit-logged.

Covers:
  - the four detectors (unit level, incl. payload 4x boundary)
  - safe applies need no token (no regression)
  - dangerous apply without token → 403 + file untouched + audit entry
  - mint → apply with token → 200 + file updated + audit entry
  - token bound to exact text, single-use, TTL-enforced
  - confirm-token refused for safe yaml; validate reports deltas
"""

import time as _time
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from proxy.routes.config import (
    _CONFIRM_TTL_S,
    _config_sha256,
    _dangerous_deltas,
)

ADMIN_KEY = "sk-admin-test-108"

STRICT_YAML = """\
server:
  port: 8090
  auth:
    enabled: true
    api_keys_env: LLM_PROXY_API_KEYS
    admin_keys_env: LLM_PROXY_ADMIN_KEYS
security:
  enabled: true
  max_payload_size_kb: 512
  firewall:
    enabled: true
  link_sanitization:
    enabled: true
    blocked_domains: ["malicious-site.com"]
endpoints: {}
"""


def _strict_config() -> dict:
    return yaml.safe_load(STRICT_YAML)


def _app(tmp_path, monkeypatch, config_yaml: str = STRICT_YAML):
    """Stub agent with auth ON and a real on-disk config file."""
    monkeypatch.setenv("LLM_PROXY_API_KEYS", ADMIN_KEY)
    monkeypatch.setenv("LLM_PROXY_ADMIN_KEYS", ADMIN_KEY)
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(config_yaml)

    agent = MagicMock()
    agent.config = yaml.safe_load(config_yaml)
    agent.config_path = str(cfg_file)
    agent._get_api_keys = MagicMock(return_value=[ADMIN_KEY])
    agent._add_log = AsyncMock()
    agent.jwt_authenticator.enabled = False
    agent._verify_admin_key = lambda token: token == ADMIN_KEY  # noqa: E731
    agent._load_config = lambda: yaml.safe_load(cfg_file.read_text())  # noqa: E731
    agent._compute_config_hash_sync = MagicMock(return_value="testhash")
    # Keep hot-reload hermetic: the real WebhookDispatcher/SecurityShield
    # constructors are beside the point here (patched per-test where needed).
    agent.webhooks = MagicMock()

    app = FastAPI()
    from proxy.routes.config import create_router as config_router

    app.include_router(config_router(agent))
    return app, agent, cfg_file


async def _post(app, path, payload, key=ADMIN_KEY):
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t", headers=headers
    ) as c:
        return await c.post(path, json=payload)


def _dangerous(auth_off=True, firewall_off=False, clear_blocklist=False, payload=None):
    cfg = _strict_config()
    if auth_off:
        cfg["server"]["auth"]["enabled"] = False
    if firewall_off:
        cfg["security"]["firewall"]["enabled"] = False
    if clear_blocklist:
        cfg["security"]["link_sanitization"]["blocked_domains"] = []
    if payload is not None:
        cfg["security"]["max_payload_size_kb"] = payload
    return yaml.safe_dump(cfg)


# ── detectors (unit) ──────────────────────────────────────────────────────


class TestDangerousDeltaDetectors:
    def test_no_delta_on_identical(self):
        assert _dangerous_deltas(_strict_config(), _strict_config()) == []

    def test_auth_disabled(self):
        new = _strict_config()
        new["server"]["auth"]["enabled"] = False
        assert _dangerous_deltas(_strict_config(), new) == ["auth-disabled"]

    def test_auth_already_off_is_not_a_delta(self):
        old = _strict_config()
        old["server"]["auth"]["enabled"] = False
        assert _dangerous_deltas(old, old) == []

    def test_firewall_disabled(self):
        new = _strict_config()
        new["security"]["firewall"]["enabled"] = False
        assert _dangerous_deltas(_strict_config(), new) == ["firewall-disabled"]

    def test_blocklist_cleared(self):
        new = _strict_config()
        new["security"]["link_sanitization"]["blocked_domains"] = []
        assert _dangerous_deltas(_strict_config(), new) == ["blocklist-cleared"]

    def test_blocklist_shrunk_but_not_empty_is_not_a_delta(self):
        assert "malicious-site.com" in _strict_config()["security"][
            "link_sanitization"
        ]["blocked_domains"]
        new = _strict_config()
        new["security"]["link_sanitization"]["blocked_domains"] = ["other.example"]
        assert _dangerous_deltas(_strict_config(), new) == []

    def test_payload_widened_beyond_4x(self):
        new = _strict_config()
        new["security"]["max_payload_size_kb"] = 4096
        assert _dangerous_deltas(_strict_config(), new) == ["payload-widened"]

    def test_payload_exactly_4x_is_not_a_delta(self):
        new = _strict_config()
        new["security"]["max_payload_size_kb"] = 2048  # 512 * 4, boundary
        assert _dangerous_deltas(_strict_config(), new) == []

    def test_multiple_deltas_all_reported(self):
        text = _dangerous(
            auth_off=True, firewall_off=True, clear_blocklist=True, payload=8192
        )
        deltas = _dangerous_deltas(_strict_config(), yaml.safe_load(text))
        assert deltas == [
            "auth-disabled",
            "firewall-disabled",
            "blocklist-cleared",
            "payload-widened",
        ]


# ── route behaviour ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_safe_apply_needs_no_token(tmp_path, monkeypatch):
    import core.security
    import core.webhooks

    app, agent, cfg_file = _app(tmp_path, monkeypatch)
    monkeypatch.setattr(core.security, "SecurityShield", MagicMock())
    monkeypatch.setattr(core.webhooks, "WebhookDispatcher", MagicMock())

    new = _strict_config()
    new["server"]["port"] = 8091
    r = await _post(app, "/api/v1/config/apply", {"yaml": yaml.safe_dump(new)})
    assert r.status_code == 200, r.text
    assert r.json()["applied"] is True
    assert yaml.safe_load(cfg_file.read_text())["server"]["port"] == 8091


@pytest.mark.asyncio
async def test_dangerous_apply_without_token_rejected(tmp_path, monkeypatch):
    app, agent, cfg_file = _app(tmp_path, monkeypatch)
    original = cfg_file.read_text()
    r = await _post(
        app, "/api/v1/config/apply", {"yaml": _dangerous(auth_off=True)}
    )
    assert r.status_code == 403, r.text
    body = r.json()
    assert body["confirm_required"] is True
    assert body["deltas"] == ["auth-disabled"]
    assert cfg_file.read_text() == original, "rejected apply must not touch disk"
    # Audit: rejection names the principal (key prefix) and the delta.
    logged = " ".join(call.args[0] for call in agent._add_log.await_args_list)
    assert "REJECTED" in logged
    assert ADMIN_KEY[:8] in logged
    assert "auth-disabled" in logged


@pytest.mark.asyncio
async def test_mint_then_apply_with_token(tmp_path, monkeypatch):
    import core.security
    import core.webhooks

    app, agent, cfg_file = _app(tmp_path, monkeypatch)
    monkeypatch.setattr(core.security, "SecurityShield", MagicMock())
    monkeypatch.setattr(core.webhooks, "WebhookDispatcher", MagicMock())

    proposed = _dangerous(auth_off=True)
    m = await _post(app, "/api/v1/config/confirm-token", {"yaml": proposed})
    assert m.status_code == 200, m.text
    token = m.json()["confirm_token"]
    assert m.json()["deltas"] == ["auth-disabled"]

    r = await _post(
        app, "/api/v1/config/apply", {"yaml": proposed, "confirm_token": token}
    )
    assert r.status_code == 200, r.text
    assert yaml.safe_load(cfg_file.read_text())["server"]["auth"]["enabled"] is False
    logged = " ".join(call.args[0] for call in agent._add_log.await_args_list)
    assert "dangerous deltas confirmed: auth-disabled" in logged


@pytest.mark.asyncio
async def test_token_bound_to_exact_text(tmp_path, monkeypatch):
    app, agent, cfg_file = _app(tmp_path, monkeypatch)
    m = await _post(
        app, "/api/v1/config/confirm-token", {"yaml": _dangerous(auth_off=True)}
    )
    token = m.json()["confirm_token"]
    # Same token, different proposal (firewall instead of auth) → rejected.
    r = await _post(
        app,
        "/api/v1/config/apply",
        {"yaml": _dangerous(auth_off=False, firewall_off=True), "confirm_token": token},
    )
    assert r.status_code == 403
    assert r.json()["confirm_required"] is True


@pytest.mark.asyncio
async def test_token_single_use(tmp_path, monkeypatch):
    import core.security
    import core.webhooks

    app, agent, cfg_file = _app(tmp_path, monkeypatch)
    monkeypatch.setattr(core.security, "SecurityShield", MagicMock())
    monkeypatch.setattr(core.webhooks, "WebhookDispatcher", MagicMock())

    proposed = _dangerous(auth_off=True)
    m = await _post(app, "/api/v1/config/confirm-token", {"yaml": proposed})
    token = m.json()["confirm_token"]
    first = await _post(
        app, "/api/v1/config/apply", {"yaml": proposed, "confirm_token": token}
    )
    assert first.status_code == 200
    # Reload flipped agent.config to auth-off; restore strict posture so the
    # second apply is dangerous again and the replay is what gets rejected.
    agent.config = _strict_config()
    second = await _post(
        app, "/api/v1/config/apply", {"yaml": proposed, "confirm_token": token}
    )
    assert second.status_code == 403


@pytest.mark.asyncio
async def test_expired_token_rejected(tmp_path, monkeypatch):
    import proxy.routes.config as config_route

    app, agent, cfg_file = _app(tmp_path, monkeypatch)
    proposed = _dangerous(auth_off=True)
    m = await _post(app, "/api/v1/config/confirm-token", {"yaml": proposed})
    token = m.json()["confirm_token"]
    real_now = _time.time()
    monkeypatch.setattr(
        config_route.time, "time", lambda: real_now + _CONFIRM_TTL_S + 60
    )
    r = await _post(
        app, "/api/v1/config/apply", {"yaml": proposed, "confirm_token": token}
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_confirm_token_refused_for_safe_yaml(tmp_path, monkeypatch):
    app, agent, cfg_file = _app(tmp_path, monkeypatch)
    new = _strict_config()
    new["server"]["port"] = 8091
    r = await _post(
        app, "/api/v1/config/confirm-token", {"yaml": yaml.safe_dump(new)}
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_validate_reports_dangerous_deltas(tmp_path, monkeypatch):
    app, agent, cfg_file = _app(tmp_path, monkeypatch)
    r = await _post(
        app, "/api/v1/config/validate", {"yaml": _dangerous(auth_off=True)}
    )
    assert r.status_code == 200
    assert r.json()["dangerous_deltas"] == ["auth-disabled"]
    r2 = await _post(app, "/api/v1/config/validate", {"yaml": STRICT_YAML})
    assert r2.json()["dangerous_deltas"] == []


def test_config_sha256_stable():
    assert _config_sha256("a") == _config_sha256("a")
    assert len(_config_sha256("a")) == 64

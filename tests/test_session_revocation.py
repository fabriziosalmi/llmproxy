"""Proxy sessions can be revoked before they expire.

A proxy JWT embeds the caller's roles and was trusted until `exp`: a user removed
in the identity provider or demoted here kept their roles, including admin
permissions on the control plane, for the rest of the hour, and the only lever
was rotating LLM_PROXY_IDENTITY_SECRET, which logs out everyone. Every token now
carries a `jti`; the list of revoked jtis and subjects is checked in
verify_proxy_jwt, persisted, and managed through POST /api/v1/identity/revoke.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import httpx
import jwt
import pytest
from fastapi import FastAPI

from core.identity import IdentityContext, IdentityManager
from core.revocation import STATE_KEY, SUBJECT_RETENTION_S, RevocationList
from tests.conftest import InMemoryRepository, minimal_config

SECRET = "s" * 40


@pytest.fixture(autouse=True)
def _identity_secret(monkeypatch):
    monkeypatch.setenv("LLM_PROXY_IDENTITY_SECRET", SECRET)


class Clock:
    def __init__(self, now=1_000_000.0):
        self.now = now

    def __call__(self):
        return self.now


def _manager():
    return IdentityManager({"identity": {"enabled": True, "providers": []}})


def _ctx(subject="alice", roles=("admin",)):
    return IdentityContext(
        provider="google", subject=subject, email=f"{subject}@x.test",
        name=subject, roles=list(roles), raw_claims={}, verified=True,
    )


def _claims(token):
    return jwt.decode(token, options={"verify_signature": False})


# ── tokens carry a jti ──────────────────────────────────────────────────────


def test_every_proxy_token_has_a_distinct_jti():
    mgr = _manager()
    a = _claims(mgr.generate_proxy_jwt(_ctx()))
    b = _claims(mgr.generate_proxy_jwt(_ctx()))

    assert a["jti"] and b["jti"] and a["jti"] != b["jti"]


def test_an_unrevoked_token_verifies_as_before():
    mgr = _manager()
    identity = mgr.verify_proxy_jwt(mgr.generate_proxy_jwt(_ctx(roles=("operator",))))

    assert identity is not None and identity.roles == ["operator"]


# ── revoking ────────────────────────────────────────────────────────────────


def test_a_revoked_jti_is_refused_and_a_sibling_session_is_not():
    mgr = _manager()
    doomed = mgr.generate_proxy_jwt(_ctx())
    sibling = mgr.generate_proxy_jwt(_ctx())

    mgr.revocations.revoke_jti(_claims(doomed)["jti"])

    assert mgr.verify_proxy_jwt(doomed) is None
    assert mgr.verify_proxy_jwt(sibling) is not None


def test_revoking_a_subject_ends_every_session_issued_so_far():
    mgr = _manager()
    first = mgr.generate_proxy_jwt(_ctx("alice"))
    second = mgr.generate_proxy_jwt(_ctx("alice"))
    other = mgr.generate_proxy_jwt(_ctx("bob"))

    mgr.revocations.revoke_subject("alice")

    assert mgr.verify_proxy_jwt(first) is None
    assert mgr.verify_proxy_jwt(second) is None
    assert mgr.verify_proxy_jwt(other) is not None


def test_a_session_minted_after_the_revocation_is_valid():
    clock = Clock()
    rev = RevocationList(clock=clock)
    rev.revoke_subject("alice")  # at t=1_000_000

    assert rev.is_revoked({"sub": "alice", "iat": 1_000_000}) is True  # same second
    assert rev.is_revoked({"sub": "alice", "iat": 999_000}) is True
    assert rev.is_revoked({"sub": "alice", "iat": 1_000_001}) is False


def test_a_token_without_iat_cannot_be_shown_to_postdate_a_subject_revocation():
    rev = RevocationList(clock=Clock())
    rev.revoke_subject("alice")
    assert rev.is_revoked({"sub": "alice"}) is True
    assert rev.is_revoked({"sub": "alice", "iat": "garbage"}) is True


def test_a_token_with_no_jti_is_not_matched_by_a_jti_entry():
    rev = RevocationList(clock=Clock())
    rev.revoke_jti("abc")
    assert rev.is_revoked({"sub": "x", "iat": 1}) is False


# ── persistence and housekeeping ────────────────────────────────────────────


def test_the_list_round_trips_through_dump_and_load():
    rev = RevocationList(clock=Clock())
    rev.revoke_jti("abc", exp=1_000_500)
    rev.revoke_subject("alice")

    reloaded = RevocationList(clock=Clock())
    reloaded.load(rev.dump())

    assert reloaded.is_revoked({"jti": "abc"})
    assert reloaded.is_revoked({"sub": "alice", "iat": 1})
    assert reloaded.summary() == {"tokens": 1, "subjects": 1}


@pytest.mark.parametrize("bad", [None, "x", [], {"jti": [1], "subject": {"a": "b"}}])
def test_malformed_persisted_state_loads_as_empty_not_as_an_error(bad):
    rev = RevocationList(clock=Clock())
    rev.revoke_jti("stale")
    rev.load(bad)
    assert rev.summary() == {"tokens": 0, "subjects": 0}


def test_entries_are_dropped_when_they_can_no_longer_matter():
    clock = Clock()
    rev = RevocationList(clock=clock)
    rev.revoke_jti("short", exp=clock.now + 60)
    rev.revoke_subject("alice")

    clock.now += 60 + 301  # past the token's expiry plus slack
    assert rev.prune() == 1 and rev.summary() == {"tokens": 0, "subjects": 1}

    clock.now += SUBJECT_RETENTION_S
    assert rev.prune() == 1 and rev.summary() == {"tokens": 0, "subjects": 0}


# ── the admin route ─────────────────────────────────────────────────────────


def _app(store=None):
    from proxy.routes.identity import create_router

    agent = MagicMock()
    agent.config = minimal_config(auth_enabled=False)
    agent.store = store or InMemoryRepository()
    agent.identity = _manager()
    agent._add_log = AsyncMock()
    app = FastAPI()
    app.include_router(create_router(agent))
    return agent, app


async def _post(app, body):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as c:
        return await c.post("/api/v1/identity/revoke", json=body)


async def test_the_route_revokes_a_subject_and_persists_it():
    agent, app = _app()
    token = agent.identity.generate_proxy_jwt(_ctx("alice"))
    assert agent.identity.verify_proxy_jwt(token) is not None

    resp = await _post(app, {"subject": "alice"})

    assert resp.status_code == 200
    assert resp.json()["status"] == "revoked" and resp.json()["subject"] == "alice"
    assert agent.identity.verify_proxy_jwt(token) is None
    saved = await agent.store.get_state(STATE_KEY)
    assert "alice" in saved["subject"]
    agent._add_log.assert_awaited()


async def test_a_restart_does_not_unrevoke(tmp_path):
    agent, app = _app()
    token = agent.identity.generate_proxy_jwt(_ctx("alice"))
    await _post(app, {"subject": "alice"})

    restarted = _manager()  # a new process: empty list...
    restarted.revocations.load(await agent.store.get_state(STATE_KEY, {}))  # ...reloaded

    assert restarted.verify_proxy_jwt(token) is None


async def test_the_route_revokes_one_jti():
    agent, app = _app()
    token = agent.identity.generate_proxy_jwt(_ctx())
    jti = _claims(token)["jti"]

    resp = await _post(app, {"jti": jti})

    assert resp.json() == {"status": "revoked", "jti": jti}
    assert agent.identity.verify_proxy_jwt(token) is None


@pytest.mark.parametrize(
    "body",
    [{}, {"subject": "a", "jti": "b"}, {"subject": ""}, {"subject": "x" * 300},
     {"jti": "j", "exp": "soon"}, [], {"subject": 5}],
)
async def test_bad_requests_are_400_and_change_nothing(body):
    agent, app = _app()

    resp = await _post(app, body)

    assert resp.status_code == 400
    assert agent.identity.revocations.summary() == {"tokens": 0, "subjects": 0}


async def test_the_summary_route_reports_counts_only():
    agent, app = _app()
    await _post(app, {"subject": "alice"})

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as c:
        resp = await c.get("/api/v1/identity/revocations")

    assert resp.json() == {"tokens": 0, "subjects": 1}


async def test_revocation_applies_to_the_data_plane_login():
    """End to end through the shared authentication helper."""
    from fastapi import HTTPException

    from proxy.auth_helpers import authenticate_data_plane

    agent = MagicMock()
    agent.config = minimal_config(auth_enabled=True)
    agent.identity = _manager()
    agent.rbac.check_permission = lambda roles, perm: True
    agent.rbac.set_user_roles = AsyncMock()
    agent._verify_api_key = lambda t: False
    agent.webhooks.dispatch = AsyncMock()
    agent.zt_manager.verify_tailscale_identity = AsyncMock(return_value={"status": "x"})
    agent._add_log = AsyncMock()
    agent._spawn_task = lambda c: asyncio.ensure_future(c)
    request = MagicMock()
    request.client.host = "1.2.3.4"
    token = agent.identity.generate_proxy_jwt(_ctx("alice"))

    assert await authenticate_data_plane(agent, request, f"Bearer {token}") == token

    agent.identity.revocations.revoke_subject("alice")
    with pytest.raises(HTTPException) as exc:
        await authenticate_data_plane(agent, request, f"Bearer {token}")
    assert exc.value.status_code == 401


async def test_revocation_applies_to_the_control_plane_principal():
    """A revoked administrator session opens nothing on /api/v1."""
    from proxy.auth_helpers import resolve_control_plane_principal

    agent = MagicMock()
    agent.identity = _manager()
    agent._verify_admin_key = lambda t: False
    agent.jwt_authenticator = None
    token = agent.identity.generate_proxy_jwt(_ctx("root", roles=("admin",)))

    assert await resolve_control_plane_principal(agent, token) is not None

    agent.identity.revocations.revoke_subject("root")

    assert await resolve_control_plane_principal(agent, token) is None

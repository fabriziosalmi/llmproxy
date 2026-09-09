"""The control plane accepted one kind of credential and checked no roles.

Two defects that could not be fixed separately.

The middleware verified an API key and nothing else, so both JWT paths this
codebase implements produced a verified identity that opened nothing: an SSO
user with roles ["admin"] got authenticated:true from /api/v1/identity/me and
401 from every other control-plane route, and the admin_auth JWT branch in the
route closures was unreachable because the middleware rejected the token before
dispatch.

Meanwhile the RBAC matrix declared fourteen permissions and exactly one,
proxy:use, was ever consulted. So admitting SSO users without checking their
roles would have made every user of a configured directory an administrator.

The compatibility property that makes this safe: an API key resolves to the
`admin` role, which holds every permission, so nothing changes for a
key-authenticated deployment.
"""

import os

import httpx
import pytest

from conftest import InMemoryRepository, minimal_config
from core.control_plane_policy import DEFAULT_PERMISSION, required_permission
from test_e2e import LightweightAgent

ADMIN_KEY = "sk-admin-control-plane"
INFERENCE_KEY = "sk-proxy-inference-only"
IDENTITY_SECRET = "an-identity-secret-long-enough-for-hs256-signing"


def _agent(identity_enabled: bool = True):
    from core.identity import IdentityManager
    from core.infisical import clear_cache
    from proxy.app_factory import create_app

    os.environ["LLM_PROXY_API_KEYS"] = INFERENCE_KEY
    os.environ["LLM_PROXY_ADMIN_KEYS"] = ADMIN_KEY
    os.environ["LLM_PROXY_IDENTITY_SECRET"] = IDENTITY_SECRET
    clear_cache()

    config = minimal_config()
    config["server"]["auth"]["enabled"] = True
    config["server"]["auth"]["api_keys_env"] = "LLM_PROXY_API_KEYS"
    config["server"]["auth"]["admin_keys_env"] = "LLM_PROXY_ADMIN_KEYS"
    config["identity"] = {"enabled": identity_enabled, "providers": []}

    agent = LightweightAgent(InMemoryRepository(), config)
    agent.identity = IdentityManager(config)
    # LightweightAgent mocks check_permission to always return True, which
    # would make every assertion below pass vacuously. Use the real matrix.
    agent.rbac.check_permission = _real_check_permission
    agent.app = create_app(agent)
    return agent


def _real_check_permission(roles, permission):
    from core.rbac import DEFAULT_PERMISSIONS

    return any(permission in DEFAULT_PERMISSIONS.get(r, set()) for r in roles)


def _jwt_for(agent, roles):
    from core.identity import IdentityContext

    return agent.identity.generate_proxy_jwt(
        IdentityContext(
            provider="google",
            subject="u1",
            email="someone@example.com",
            name="Someone",
            roles=list(roles),
            verified=True,
        ),
        ttl=3600,
    )


def _client(agent):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=agent.app), base_url="http://test"
    )


# ── the finding: a verified identity opened nothing ─────────────────────────


@pytest.mark.asyncio
async def test_an_sso_admin_reaches_the_control_plane():
    """This returned 401 on every route while /identity/me said authenticated."""
    agent = _agent()
    token = _jwt_for(agent, ["admin"])

    async with _client(agent) as c:
        headers = {"Authorization": f"Bearer {token}"}
        me = await c.get("/api/v1/identity/me", headers=headers)
        registry = await c.get("/api/v1/registry", headers=headers)

    assert me.json()["authenticated"] is True
    assert registry.status_code == 200, (
        "an identity the proxy verified as admin still cannot read the registry"
    )


@pytest.mark.asyncio
async def test_an_sso_admin_can_write():
    agent = _agent()
    token = _jwt_for(agent, ["admin"])

    async with _client(agent) as c:
        resp = await c.post(
            "/api/v1/proxy/toggle",
            headers={"Authorization": f"Bearer {token}"},
            json={"enabled": True},
        )

    assert resp.status_code == 200


# ── and admitting them did not make everyone an admin ───────────────────────


@pytest.mark.asyncio
async def test_a_viewer_can_read_the_registry():
    agent = _agent()
    token = _jwt_for(agent, ["viewer"])

    async with _client(agent) as c:
        resp = await c.get(
            "/api/v1/registry", headers={"Authorization": f"Bearer {token}"}
        )

    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_a_viewer_cannot_install_a_plugin():
    """The distinction an operator assigning `viewer` would assume exists."""
    agent = _agent()
    token = _jwt_for(agent, ["viewer"])

    async with _client(agent) as c:
        resp = await c.post(
            "/api/v1/plugins/install",
            headers={"Authorization": f"Bearer {token}"},
            json={"name": "x", "hook": "PRE_FLIGHT", "entrypoint": "a:b"},
        )

    assert resp.status_code == 403
    assert "plugins:manage" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_a_viewer_cannot_purge_the_audit_log():
    agent = _agent()
    token = _jwt_for(agent, ["viewer"])

    async with _client(agent) as c:
        resp = await c.get(
            "/api/v1/gdpr/retention", headers={"Authorization": f"Bearer {token}"}
        )

    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_a_viewer_cannot_read_the_role_matrix():
    """Who may do what is administrator-only, reads included."""
    agent = _agent()
    token = _jwt_for(agent, ["viewer"])

    async with _client(agent) as c:
        resp = await c.get(
            "/api/v1/rbac/roles", headers={"Authorization": f"Bearer {token}"}
        )

    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_an_operator_can_toggle_but_not_manage_users():
    agent = _agent()
    token = _jwt_for(agent, ["operator"])

    async with _client(agent) as c:
        headers = {"Authorization": f"Bearer {token}"}
        allowed = await c.post(
            "/api/v1/proxy/toggle", headers=headers, json={"enabled": True}
        )
        refused = await c.get("/api/v1/rbac/roles", headers=headers)

    assert allowed.status_code == 200
    assert refused.status_code == 403


# ── the compatibility property ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_admin_key_is_unaffected():
    """The whole change rests on this: keys resolve to `admin`, which holds
    every permission, so no key-authenticated deployment changes behaviour."""
    agent = _agent()

    async with _client(agent) as c:
        headers = {"Authorization": f"Bearer {ADMIN_KEY}"}
        for path in (
            "/api/v1/registry",
            "/api/v1/rbac/roles",
            "/api/v1/plugins",
            "/api/v1/webhooks",
        ):
            resp = await c.get(path, headers=headers)
            assert resp.status_code == 200, f"{path} -> {resp.status_code}"


@pytest.mark.asyncio
async def test_an_inference_key_is_still_refused():
    """Adding a second credential type must not reopen the tier."""
    agent = _agent()

    async with _client(agent) as c:
        resp = await c.get(
            "/api/v1/registry", headers={"Authorization": f"Bearer {INFERENCE_KEY}"}
        )

    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_an_unsigned_token_is_refused():
    agent = _agent()

    async with _client(agent) as c:
        resp = await c.get(
            "/api/v1/registry",
            headers={"Authorization": "Bearer eyJhbGciOiJub25lIn0.e30."},
        )

    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_a_jwt_is_refused_when_identity_is_disabled():
    """No SSO configured means no SSO principal, whatever the token says."""
    agent = _agent(identity_enabled=False)
    # Mint against a manager that IS enabled, then present it to one that is not.
    enabled = _agent(identity_enabled=True)
    token = _jwt_for(enabled, ["admin"])

    async with _client(agent) as c:
        resp = await c.get(
            "/api/v1/registry", headers={"Authorization": f"Bearer {token}"}
        )

    assert resp.status_code == 401


# ── the permission map itself ───────────────────────────────────────────────


@pytest.mark.parametrize(
    "method,path,expected",
    [
        ("GET", "/api/v1/registry", "registry:read"),
        ("POST", "/api/v1/registry", "registry:write"),
        ("DELETE", "/api/v1/registry/openai", "registry:write"),
        ("GET", "/api/v1/plugins", "registry:read"),
        ("POST", "/api/v1/plugins/install", "plugins:manage"),
        ("GET", "/api/v1/logs", "logs:read"),
        ("POST", "/api/v1/config/apply", "proxy:config"),
        ("GET", "/api/v1/rbac/roles", "users:manage"),
        ("POST", "/api/v1/gdpr/erase", "users:manage"),
        ("GET", "/api/v1/webhooks", "users:manage"),
        ("GET", "/metrics", "logs:read"),
    ],
)
def test_the_permission_map(method, path, expected):
    assert required_permission(method, path) == expected


def test_an_unmatched_control_plane_route_is_administrator_only():
    """Deny-by-default survives: a route added later is closed to lesser roles
    until someone names it, which is the property the middleware was built on."""
    from core.rbac import DEFAULT_PERMISSIONS

    perm = required_permission("POST", "/api/v1/something-invented-tomorrow")

    assert perm == DEFAULT_PERMISSION
    holders = [r for r, p in DEFAULT_PERMISSIONS.items() if perm in p]
    assert holders == ["admin"], f"{perm} is held by more than admin: {holders}"


def test_every_permission_the_map_names_is_one_a_role_holds():
    """A permission no role holds is a permission that only ever denies."""
    from core.control_plane_policy import _RULES
    from core.rbac import DEFAULT_PERMISSIONS

    known = set().union(*DEFAULT_PERMISSIONS.values())
    named = {p for _, read, write in _RULES for p in (read, write)}

    assert named <= known, f"invented permissions: {sorted(named - known)}"


def test_the_admin_role_holds_everything_the_map_can_require():
    """The compatibility property, asserted rather than assumed."""
    from core.control_plane_policy import _RULES
    from core.rbac import DEFAULT_PERMISSIONS

    named = {p for _, read, write in _RULES for p in (read, write)}
    named.add(DEFAULT_PERMISSION)

    assert named <= DEFAULT_PERMISSIONS["admin"]


# ── the per-route closures no longer contradict the middleware ──────────────


def test_no_route_closure_re_checks_only_the_key():
    """Each closure re-verified the ADMIN KEY, so a caller the middleware had
    just admitted on a JWT was refused one layer later — the same mismatch that
    made the log stream unreachable in a two-tier deployment."""
    import inspect

    from proxy.routes import admin, config, gdpr, plugins, registry, telemetry

    for module in (admin, config, gdpr, plugins, registry, telemetry):
        source = inspect.getsource(module)
        assert "principal_already_verified" in source, (
            f"{module.__name__} does not defer to the middleware's verdict"
        )

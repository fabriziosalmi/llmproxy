"""An inference key must not reach the control plane.

The two-tier key model (LLM_PROXY_API_KEYS for /v1/, LLM_PROXY_ADMIN_KEYS for
/api/v1/) was enforced only by the per-route _check_admin_auth() closures, and
seventeen control-plane routes never called one — the global middleware
verified against the *inference* bag, so it proved the caller held some key and
nothing more. GET /api/v1/registry returned every upstream URL to any client
key, as did the webhook configuration, the plugin inventory and the RBAC role
matrix, in deployments that had correctly segregated the two bags.

The middleware now verifies the admin bag, so the tier is enforced structurally
rather than per handler. This walks the registered routes instead of listing
them, so a route added later is covered without anyone remembering to add it.
"""

import httpx
import pytest
import pytest_asyncio

from conftest import InMemoryRepository, minimal_config
from test_e2e import LightweightAgent

INFERENCE_KEY = "sk-proxy-inference-only"
ADMIN_KEY = "sk-admin-control-plane"


def _agent_with_real_app(inference_key: str, admin_key: str | None):
    """Build the agent behind the REAL middleware stack.

    LightweightAgent assembles a bare FastAPI and includes the routers, so its
    `.app` has no global_admin_auth at all — which is precisely why the
    seventeen unguarded routes were invisible to the suite. Going through
    create_app puts the middleware under test instead of around it.
    """
    import os

    from core.infisical import clear_cache
    from proxy.app_factory import create_app

    os.environ["LLM_PROXY_API_KEYS"] = inference_key
    if admin_key is None:
        os.environ.pop("LLM_PROXY_ADMIN_KEYS", None)
    else:
        os.environ["LLM_PROXY_ADMIN_KEYS"] = admin_key
    # get_secret memoises resolved values process-wide, so a key read by an
    # earlier test survives deleting the environment variable.
    clear_cache()

    config = minimal_config()
    config["server"]["auth"]["enabled"] = True
    # minimal_config points api_keys_env at LLM_PROXY_TEST_KEYS; name both bags
    # explicitly so this test is about the tier and not about which variable
    # the shared fixture happens to use.
    config["server"]["auth"]["api_keys_env"] = "LLM_PROXY_API_KEYS"
    config["server"]["auth"]["admin_keys_env"] = "LLM_PROXY_ADMIN_KEYS"
    agent = LightweightAgent(InMemoryRepository(), config)
    agent.app = create_app(agent)
    return agent


@pytest.fixture
def agent(monkeypatch):
    monkeypatch.setenv("LLM_PROXY_API_KEYS", INFERENCE_KEY)
    monkeypatch.setenv("LLM_PROXY_ADMIN_KEYS", ADMIN_KEY)
    return _agent_with_real_app(INFERENCE_KEY, ADMIN_KEY)


@pytest_asyncio.fixture
async def client(agent):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=agent.app), base_url="http://test"
    ) as c:
        yield c


def _control_plane_get_paths(agent) -> list[str]:
    """Registered GET routes under /api/v1/ that take no path parameter.

    Parameterised routes are excluded only because a synthetic id would make
    the assertion about the handler's 404 rather than about the middleware.
    """
    from proxy.app_factory import _PUBLIC_EXACT

    paths = []
    for route in agent.app.routes:
        path = getattr(route, "path", "")
        methods = getattr(route, "methods", set()) or set()
        if not path.startswith("/api/v1/") or "GET" not in methods:
            continue
        if "{" in path or path in _PUBLIC_EXACT:
            continue
        paths.append(path)
    return sorted(set(paths))


@pytest.mark.asyncio
async def test_there_are_control_plane_routes_to_check(agent):
    """Guard the guard: an empty sweep would make the next test vacuous."""
    assert len(_control_plane_get_paths(agent)) >= 10


@pytest.mark.asyncio
async def test_no_control_plane_route_accepts_an_inference_key(client, agent):
    """The finding, as a sweep: every /api/v1/ GET must refuse a client key."""
    leaked = []
    for path in _control_plane_get_paths(agent):
        resp = await client.get(
            path, headers={"Authorization": f"Bearer {INFERENCE_KEY}"}
        )
        if resp.status_code not in (401, 403):
            leaked.append(f"{path} -> {resp.status_code}")

    assert not leaked, (
        "control-plane routes reachable with an inference key:\n  "
        + "\n  ".join(leaked)
    )


@pytest.mark.asyncio
async def test_the_registry_does_not_hand_upstream_urls_to_a_client_key(client):
    """The sharpest instance, pinned on its own so the reason survives."""
    resp = await client.get(
        "/api/v1/registry", headers={"Authorization": f"Bearer {INFERENCE_KEY}"}
    )
    assert resp.status_code in (401, 403)


@pytest.mark.asyncio
async def test_an_admin_key_still_reaches_the_control_plane(client):
    """The tier must separate, not just deny."""
    resp = await client.get(
        "/api/v1/registry", headers={"Authorization": f"Bearer {ADMIN_KEY}"}
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_single_tier_deployments_are_unaffected(monkeypatch):
    """With no admin bag configured, the inference key still works.

    verify_admin_key falls back to the inference keys when LLM_PROXY_ADMIN_KEYS
    is unset, so tightening the middleware must not break the many deployments
    that never segregated the two.
    """
    monkeypatch.setenv("LLM_PROXY_API_KEYS", INFERENCE_KEY)
    monkeypatch.delenv("LLM_PROXY_ADMIN_KEYS", raising=False)
    single_tier = _agent_with_real_app(INFERENCE_KEY, None)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=single_tier.app), base_url="http://test"
    ) as c:
        resp = await c.get(
            "/api/v1/registry", headers={"Authorization": f"Bearer {INFERENCE_KEY}"}
        )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_health_stays_public(client):
    """The container healthcheck and both k8s probes poll it unauthenticated."""
    assert (await client.get("/health")).status_code == 200

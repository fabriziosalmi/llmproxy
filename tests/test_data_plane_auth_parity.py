"""chat, completions and embeddings authenticate through one implementation.

Each route carried its own copy of the auth block and the copies drifted: only
chat counted missing-key, empty-token and invalid-key failures, so a client
brute-forcing keys against /v1/completions moved no
llm_proxy_auth_failures_total counter, and a config with no server.auth section
(which the startup validator and the control-plane middleware treat as
"authentication ON") made all three raise KeyError and return 500.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from tests.conftest import InMemoryRepository, minimal_config

VALID_KEY = "sk-proxy-" + "c" * 32

ROUTES = [
    ("/v1/chat/completions", {"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]}),
    ("/v1/completions", {"model": "gpt-4o", "prompt": "hi"}),
    ("/v1/embeddings", {"model": "text-embedding-3-small", "input": "hi"}),
]


def _agent(config):
    agent = MagicMock()
    agent.config = config
    agent.store = InMemoryRepository()
    agent._verify_api_key = lambda t: t == VALID_KEY
    agent.identity = MagicMock()
    agent.identity.enabled = False
    agent.webhooks.dispatch = AsyncMock()
    agent.zt_manager.verify_tailscale_identity = AsyncMock(
        return_value={"status": "unverified"}
    )
    agent._add_log = AsyncMock()
    agent.rbac.check_quota = AsyncMock(return_value=True)
    agent.proxy_enabled = False  # stops chat after auth, before any upstream work

    def _spawn(coro):
        return asyncio.ensure_future(coro)

    agent._spawn_task = _spawn
    return agent


def _client(agent):
    from proxy.routes.chat import create_router as chat
    from proxy.routes.completions import create_router as completions
    from proxy.routes.embeddings import create_router as embeddings

    app = FastAPI()
    for make in (chat, completions, embeddings):
        app.include_router(make(agent))
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


def _config(**auth):
    cfg = minimal_config(auth_enabled=True)
    cfg["server"]["auth"].update(auth)
    return cfg


@pytest.mark.parametrize("path,body", ROUTES)
@pytest.mark.parametrize(
    "headers,reason,status",
    [
        ({}, "missing_key", 401),
        ({"Authorization": "Bearer sk-proxy-wrong"}, "invalid_key", 401),
    ],
)
async def test_every_route_counts_the_same_auth_failures(
    path, body, headers, reason, status
):
    async with _client(_agent(_config())) as client:
        with patch("core.metrics.MetricsTracker.track_auth_failure") as counted:
            resp = await client.post(path, json=body, headers=headers)

    assert resp.status_code == status
    counted.assert_called_once_with(reason)


@pytest.mark.parametrize("path,body", ROUTES)
async def test_a_config_without_an_auth_section_requires_authentication(path, body):
    cfg = minimal_config(auth_enabled=True)
    del cfg["server"]["auth"]
    async with _client(_agent(cfg)) as client:
        resp = await client.post(path, json=body)

    # auth_enabled() defaults to ON: refused, not a KeyError -> 500.
    assert resp.status_code == 401


async def test_auth_disabled_returns_no_token_and_counts_nothing():
    from proxy.auth_helpers import authenticate_data_plane

    request = MagicMock()
    with patch("core.metrics.MetricsTracker.track_auth_failure") as counted:
        token = await authenticate_data_plane(
            _agent(minimal_config(auth_enabled=False)), request, None
        )

    assert token == ""
    counted.assert_not_called()


async def test_a_whitespace_only_credential_is_an_empty_token():
    # Not reachable from a parsed HTTP header (the server trims it to "no
    # header"), but the helper is the boundary, so it is checked directly.
    from fastapi import HTTPException

    from proxy.auth_helpers import authenticate_data_plane

    request = MagicMock()
    request.client.host = "1.2.3.4"
    with patch("core.metrics.MetricsTracker.track_auth_failure") as counted:
        with pytest.raises(HTTPException) as exc:
            await authenticate_data_plane(_agent(_config()), request, "   ")

    assert exc.value.status_code == 401
    counted.assert_called_once_with("empty_token")


async def test_a_valid_key_returns_the_token():
    from proxy.auth_helpers import authenticate_data_plane

    request = MagicMock()
    request.client.host = "1.2.3.4"
    token = await authenticate_data_plane(
        _agent(_config()), request, f"Bearer {VALID_KEY}"
    )
    assert token == VALID_KEY


@pytest.mark.parametrize("path,body", ROUTES)
async def test_a_failed_login_dispatches_the_auth_failure_webhook_on_every_route(
    path, body
):
    agent = _agent(_config())
    async with _client(agent) as client:
        await client.post(
            path, json=body, headers={"Authorization": "Bearer sk-proxy-wrong"}
        )
        await asyncio.sleep(0)

    agent.webhooks.dispatch.assert_called_once()
    assert agent.webhooks.dispatch.call_args.args[1]["reason"] == "invalid_api_key"


async def test_exhausted_quota_is_a_402_on_embeddings_and_a_flag_on_the_others():
    agent = _agent(_config())
    agent.rbac.check_quota = AsyncMock(return_value=False)
    headers = {"Authorization": f"Bearer {VALID_KEY}"}

    async with _client(agent) as client:
        emb = await client.post(ROUTES[2][0], json=ROUTES[2][1], headers=headers)
        chat = await client.post(ROUTES[0][0], json=ROUTES[0][1], headers=headers)

    assert emb.status_code == 402
    # chat passes authentication (the pipeline enforces quota later); here the
    # proxy is stopped, so it is refused for that reason, not for auth.
    assert chat.status_code == 503

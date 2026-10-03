"""
Regression tests for the /v1/embeddings quota bypass (fixed in 1.36.2).

The embeddings route set `request.state.quota_exceeded = True` when
`RBACManager.check_quota` failed but never read the flag back — only
`request_pipeline` consumes it. Over-quota keys were served without limit.

These tests drive the real route handler with a stub agent:

  - quota exhausted  → 402, upstream never touched, BUDGET_THRESHOLD fired
  - quota available  → 200, upstream called exactly once
  - SecurityShield  → called with ip + key_prefix (ThreatLedger parity)
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import httpx
from fastapi import FastAPI
from starlette.responses import JSONResponse

from core.session_id import from_token as session_id_from_token
from proxy.routes.embeddings import create_router

TEST_KEY = "sk-proxy-test-quota"
UPSTREAM_BODY = {
    "object": "list",
    "data": [
        {
            "object": "embedding",
            "embedding": [0.1, 0.2, 0.3],
            "index": 0,
        }
    ],
    "model": "text-embedding-3-small",
    "usage": {"prompt_tokens": 8, "total_tokens": 8},
}


def _make_agent(monkeypatch, *, quota_ok: bool):
    """Stub agent exposing exactly what the embeddings route touches."""
    monkeypatch.setenv("LLM_PROXY_API_KEYS", TEST_KEY)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-provider-dummy")

    agent = MagicMock()
    agent.config = {
        "server": {"auth": {"enabled": True}},
        "endpoints": {
            "openai": {
                "provider": "openai",
                "base_url": "https://api.openai.com/v1",
                "api_key_env": "OPENAI_API_KEY",
            },
        },
    }
    agent.security.inspect = AsyncMock(return_value=None)
    agent.identity.enabled = False
    agent.rbac.check_quota = AsyncMock(return_value=quota_ok)
    agent.webhooks.dispatch = AsyncMock()
    # Authentication goes through proxy.auth_helpers.authenticate_data_plane,
    # which (as for chat) also checks the Tailscale identity and logs.
    agent.zt_manager.verify_tailscale_identity = AsyncMock(
        return_value={"status": "unverified"}
    )
    agent._add_log = AsyncMock()
    agent._budget_lock = asyncio.Lock()
    agent.total_cost_today = 0.0
    agent._background_tasks: set[asyncio.Task] = set()

    def _spawn_task(coro):
        task = asyncio.create_task(coro)
        agent._background_tasks.add(task)
        task.add_done_callback(agent._background_tasks.discard)
        return task

    agent._spawn_task = _spawn_task
    agent.enqueue_write = MagicMock()
    agent._verify_api_key = lambda token: token == TEST_KEY  # noqa: E731
    agent._get_session = AsyncMock(return_value=MagicMock())
    return agent


def _make_client(agent):
    app = FastAPI(title="LLMPROXY-EMBEDDINGS-QUOTA-TEST")
    app.include_router(create_router(agent))
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),  # type: ignore[arg-type]
        base_url="http://test",
    )


def _mock_adapter(monkeypatch):
    """Upstream adapter double; returns True if it was hit."""
    import proxy.routes.embeddings as embeddings_route

    hits: list = []
    adapter = MagicMock()
    adapter.provider_name = "openai"
    adapter.supports_embeddings = True
    adapter.translate_embedding_request = MagicMock(
        return_value=("https://api.openai.com/v1/embeddings", {}, {})
    )
    # Identity: the route keeps the upstream response object as-is.
    adapter.translate_embedding_response = MagicMock(side_effect=lambda data: data)

    async def _request(url, body, headers, session):
        hits.append((url, body))
        return JSONResponse(content=UPSTREAM_BODY, status_code=200)

    adapter.request = _request
    monkeypatch.setattr(embeddings_route, "get_adapter", MagicMock(return_value=adapter))
    return hits


async def _drain(agent):
    """Let _spawn_task'd webhook dispatches finish before asserting."""
    if agent._background_tasks:
        await asyncio.gather(*list(agent._background_tasks), return_exceptions=True)


class TestEmbeddingsQuotaEnforcement:
    async def test_quota_exhausted_returns_402(self, monkeypatch):
        agent = _make_agent(monkeypatch, quota_ok=False)
        hits = _mock_adapter(monkeypatch)
        async with _make_client(agent) as client:
            resp = await client.post(
                "/v1/embeddings",
                headers={"Authorization": f"Bearer {TEST_KEY}"},
                json={"model": "text-embedding-3-small", "input": "hello"},
            )
        await _drain(agent)
        assert resp.status_code == 402, resp.text
        assert "Budget Exceeded" in resp.text
        assert hits == [], "upstream must not be touched for over-quota keys"

    async def test_quota_exhausted_fires_budget_webhook(self, monkeypatch):
        agent = _make_agent(monkeypatch, quota_ok=False)
        _mock_adapter(monkeypatch)
        async with _make_client(agent) as client:
            await client.post(
                "/v1/embeddings",
                headers={"Authorization": f"Bearer {TEST_KEY}"},
                json={"model": "text-embedding-3-small", "input": "hello"},
            )
        await _drain(agent)
        reasons = [
            call.args[1].get("reason", "")
            for call in agent.webhooks.dispatch.await_args_list
        ]
        assert "quota_exceeded" in reasons

    async def test_quota_available_forwards_once(self, monkeypatch):
        agent = _make_agent(monkeypatch, quota_ok=True)
        hits = _mock_adapter(monkeypatch)
        async with _make_client(agent) as client:
            resp = await client.post(
                "/v1/embeddings",
                headers={"Authorization": f"Bearer {TEST_KEY}"},
                json={"model": "text-embedding-3-small", "input": "hello"},
            )
        assert resp.status_code == 200, resp.text
        assert len(hits) == 1
        payload = json.loads(resp.text)
        assert payload["data"][0]["embedding"] == [0.1, 0.2, 0.3]

    async def test_shield_receives_ip_and_key_prefix(self, monkeypatch):
        agent = _make_agent(monkeypatch, quota_ok=True)
        _mock_adapter(monkeypatch)
        async with _make_client(agent) as client:
            await client.post(
                "/v1/embeddings",
                headers={"Authorization": f"Bearer {TEST_KEY}"},
                json={"model": "text-embedding-3-small", "input": "hello"},
            )
        (call,) = agent.security.inspect.await_args_list
        assert call.kwargs.get("ip") == "127.0.0.1"
        expected_session = session_id_from_token(TEST_KEY)
        assert call.args[1] == expected_session
        assert call.kwargs.get("key_prefix") == expected_session[:8]

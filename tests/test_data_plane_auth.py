"""The OpenAI-compatible /v1/ routes must each authenticate.

The global middleware denies /api/v1/ and /admin/ by prefix, and deliberately
does not cover /v1/: the data plane accepts a JWT as well as an API key, and
the middleware only checks the latter, so protecting /v1/ there would reject
valid JWT callers. That leaves each /v1/ handler responsible for its own check.

/v1/models was written without one. With auth enabled and no credential it
returned 200 and the configured provider and model inventory, while every
sibling returned 401 — reconnaissance for an attacker and configuration an
operator would assume was private.
"""

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from unittest.mock import MagicMock

from tests.conftest import InMemoryRepository, minimal_config

VALID_KEY = "sk-proxy-" + "b" * 32


def _app(auth_enabled: bool):
    agent = MagicMock()
    cfg = minimal_config(auth_enabled=auth_enabled)
    cfg["endpoints"] = {
        "openai": {"provider": "openai", "models": ["gpt-4o", "gpt-4o-mini"]}
    }
    agent.config = cfg
    agent.store = InMemoryRepository()
    agent._verify_api_key = lambda t: t == VALID_KEY
    agent.identity = MagicMock()
    agent.identity.enabled = False

    app = FastAPI()
    from proxy.routes.models import create_router

    app.include_router(create_router(agent))
    return app


async def _get(app, path, headers=None):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as client:
        return await client.get(path, headers=headers or {})


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/v1/models", "/v1/models/gpt-4o"])
async def test_models_require_a_credential_when_auth_is_on(path):
    """The defect: this returned 200 with the model inventory."""
    resp = await _get(_app(auth_enabled=True), path)
    assert resp.status_code == 401, (
        f"{path} served without credentials while auth is enabled; "
        f"body was {resp.text[:120]}"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/v1/models", "/v1/models/gpt-4o"])
async def test_models_reject_a_wrong_key(path):
    resp = await _get(
        _app(auth_enabled=True), path, {"Authorization": "Bearer sk-proxy-wrong"}
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_models_accept_a_valid_key():
    resp = await _get(
        _app(auth_enabled=True), "/v1/models", {"Authorization": f"Bearer {VALID_KEY}"}
    )
    assert resp.status_code == 200
    assert [m["id"] for m in resp.json()["data"]] == ["gpt-4o", "gpt-4o-mini"]


@pytest.mark.asyncio
async def test_models_are_open_when_auth_is_disabled():
    """Development mode must keep working without a credential."""
    resp = await _get(_app(auth_enabled=False), "/v1/models")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_model_inventory_is_not_disclosed_in_the_401_body():
    """A rejection must not leak what it was protecting."""
    resp = await _get(_app(auth_enabled=True), "/v1/models")
    assert "gpt-4o" not in resp.text

"""A streaming request whose upstream fails is a failure, not a 200.

The adapters' stream() handed whatever the upstream sent to the client as stream
content, status included. A 429 or 503 therefore reached the caller as the body
of a 200 response, the circuit breaker saw a first chunk and reported success,
the fallback chain was never tried, and the audit row said 200.

These run the real adapters and the real forwarder against local upstream
servers, because the failure only exists when an actual HTTP status arrives
before the first byte of the body.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from fastapi import HTTPException
from starlette.responses import Response, StreamingResponse

from core.circuit_breaker import CircuitManager
from proxy.adapters.anthropic import AnthropicAdapter
from proxy.adapters.azure import AzureAdapter
from proxy.adapters.base import UpstreamStatusError
from proxy.adapters.google import GoogleAdapter
from proxy.adapters.openai import OpenAIAdapter
from proxy.forwarder import RequestForwarder

SSE = b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\ndata: [DONE]\n\n'


def _upstream(status: int, body: bytes = b'{"error":{"message":"nope"}}'):
    """An app answering every POST with `status` (an SSE stream when 200)."""

    async def handler(request: web.Request) -> web.StreamResponse:
        if status != 200:
            return web.Response(
                status=status, body=body, content_type="application/json"
            )
        resp = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        await resp.write(SSE)
        await resp.write_eof()
        return resp

    app = web.Application()
    app.router.add_route("POST", "/{tail:.*}", handler)
    return app


@pytest.fixture
async def servers():
    started: list[TestServer] = []

    async def start(status, body=b'{"error":{"message":"nope"}}'):
        server = TestServer(_upstream(status, body))
        await server.start_server()
        started.append(server)
        return str(server.make_url("")).rstrip("/")

    yield start
    for server in started:
        await server.close()


@pytest.fixture
async def session():
    async with aiohttp.ClientSession() as s:
        yield s


def _ctx():
    return SimpleNamespace(
        body={"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
        metadata={},
        response=None,
        session_id="test",
        state=None,
    )


def _forwarder(config):
    return RequestForwarder(
        config=config,
        circuit_manager=CircuitManager(),
        budget_lock=asyncio.Lock(),
        get_session=AsyncMock(),
        add_log=AsyncMock(),
    )


def _target(base_url):
    return SimpleNamespace(
        id="openai", url=base_url, provider="openai", provider_type="openai"
    )


async def _read(response: StreamingResponse) -> bytes:
    return b"".join([c async for c in response.body_iterator])


async def _stream_request(ctx, fwd, base_url, session):
    ctx.body["stream"] = True
    return await fwd.forward_with_fallback(ctx, _target(base_url), {}, session, {})


# ── the adapters ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "adapter", [OpenAIAdapter(), AnthropicAdapter(), GoogleAdapter(), AzureAdapter()]
)
async def test_every_adapter_raises_before_yielding_an_error_status(
    adapter, servers, session
):
    base = await servers(503, b'{"error":"overloaded"}')

    stream = adapter.stream(f"{base}/v1/x", {}, {}, session)
    with pytest.raises(UpstreamStatusError) as caught:
        await anext(stream)

    assert caught.value.status == 503
    assert caught.value.content == b'{"error":"overloaded"}'
    assert caught.value.media_type == "application/json"


async def test_a_good_stream_is_untouched(servers, session):
    base = await servers(200)

    chunks = [c async for c in OpenAIAdapter().stream(f"{base}/v1/x", {}, {}, session)]

    assert b"".join(chunks) == SSE


# ── the forwarder ─────────────────────────────────────────────────────────────


async def test_upstream_503_falls_back_instead_of_streaming_the_error(
    servers, session
):
    primary = await servers(503)
    backup = await servers(200)
    config = {
        "endpoints": {"groq": {"provider": "groq", "base_url": backup}},
        "fallback_chains": {"gpt-4o": [{"provider": "groq", "model": "llama"}]},
    }
    fwd = _forwarder(config)
    ctx = _ctx()

    response = await _stream_request(ctx, fwd, primary, session)

    assert isinstance(response, StreamingResponse)
    assert await _read(response) == SSE
    assert ctx.metadata["_fallback_used"] == "groq"
    breaker = await fwd.circuit_manager.get_breaker("openai")
    assert breaker.failure_count == 1


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
async def test_retryable_status_without_a_fallback_is_raised(status, servers, session):
    base = await servers(status)
    fwd = _forwarder({})

    with pytest.raises(HTTPException) as caught:
        await _stream_request(_ctx(), fwd, base, session)

    assert caught.value.status_code == status
    breaker = await fwd.circuit_manager.get_breaker("openai")
    assert breaker.failure_count == 1


async def test_client_error_is_relayed_with_the_upstream_body_and_no_fallback(
    servers, session
):
    primary = await servers(401, b'{"error":{"message":"bad key"}}')
    backup = await servers(200)
    config = {
        "endpoints": {"groq": {"provider": "groq", "base_url": backup}},
        "fallback_chains": {"gpt-4o": [{"provider": "groq", "model": "llama"}]},
    }
    fwd = _forwarder(config)
    ctx = _ctx()

    response = await _stream_request(ctx, fwd, primary, session)

    assert isinstance(response, Response) and not isinstance(
        response, StreamingResponse
    )
    assert response.status_code == 401
    assert response.body == b'{"error":{"message":"bad key"}}'
    assert "_fallback_used" not in ctx.metadata
    breaker = await fwd.circuit_manager.get_breaker("openai")
    assert breaker.failure_count == 0


async def test_unreachable_upstream_falls_back(servers, session):
    backup = await servers(200)
    config = {
        "endpoints": {"groq": {"provider": "groq", "base_url": backup}},
        "fallback_chains": {"gpt-4o": [{"provider": "groq", "model": "llama"}]},
    }
    fwd = _forwarder(config)

    # Nothing listens on port 9 (discard); the connect fails before any status.
    response = await _stream_request(_ctx(), fwd, "http://127.0.0.1:9", session)

    assert await _read(response) == SSE


async def test_a_good_stream_reaches_the_client_and_closes_the_breaker(
    servers, session
):
    base = await servers(200)
    fwd = _forwarder({})

    response = await _stream_request(_ctx(), fwd, base, session)

    assert await _read(response) == SSE
    breaker = await fwd.circuit_manager.get_breaker("openai")
    assert breaker.failure_count == 0

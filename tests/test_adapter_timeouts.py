"""The adapters honour the session's timeout policy instead of overriding it.

Every adapter passed ``ClientTimeout(total=60, sock_read=55)`` on each request,
which beats the session-level timeout aiohttp would otherwise apply. So
``server.timeout`` / ``server.total_timeout`` / ``connect_timeout`` did nothing
for upstream calls, long generations and SSE streams were cut at 60 seconds
(contradicting the documented "no ceiling" default), and because the forwarder
retries a timeout on the next provider, the caller then waited twice while two
providers billed. proxy/http_session.py is the one place the policy lives.
"""

import asyncio

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from proxy.adapters.anthropic import AnthropicAdapter
from proxy.adapters.azure import AzureAdapter
from proxy.adapters.google import GoogleAdapter
from proxy.adapters.openai import OpenAIAdapter

ADAPTERS = [OpenAIAdapter(), AnthropicAdapter(), GoogleAdapter(), AzureAdapter()]


@pytest.fixture
async def slow_server():
    async def handler(request):
        await asyncio.sleep(0.8)
        return web.json_response({"ok": True})

    app = web.Application()
    app.router.add_route("POST", "/{tail:.*}", handler)
    server = TestServer(app)
    await server.start_server()
    yield str(server.make_url("")).rstrip("/")
    await server.close()


@pytest.mark.parametrize("adapter", ADAPTERS, ids=lambda a: type(a).__name__)
async def test_request_obeys_a_short_session_timeout(adapter, slow_server):
    timeout = aiohttp.ClientTimeout(total=0.3)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        with pytest.raises(TimeoutError):
            await adapter.request(f"{slow_server}/x", {}, {}, session)


@pytest.mark.parametrize("adapter", ADAPTERS, ids=lambda a: type(a).__name__)
async def test_stream_obeys_a_short_session_timeout(adapter, slow_server):
    timeout = aiohttp.ClientTimeout(total=0.3)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        with pytest.raises(TimeoutError):
            async for _ in adapter.stream(f"{slow_server}/x", {}, {}, session):
                pass


@pytest.mark.parametrize("adapter", ADAPTERS, ids=lambda a: type(a).__name__)
async def test_no_overall_ceiling_means_no_ceiling(adapter, slow_server):
    """The documented default (total_timeout unset) must not become 60 s."""
    timeout = aiohttp.ClientTimeout(total=None, sock_read=5)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        response = await adapter.request(f"{slow_server}/x", {}, {}, session)
    assert response.status_code == 200


def test_no_adapter_carries_a_private_timeout():
    for adapter in ADAPTERS:
        assert not hasattr(adapter, "_REQUEST_TIMEOUT"), type(adapter).__name__

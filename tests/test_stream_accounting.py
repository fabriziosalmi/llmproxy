"""A finished stream is accounted for as what it was.

The generator's ``finally`` did the charging and the spend/audit writes inline.
When a client disconnects Starlette cancels the generator, and the first real
await in that block was interrupted: the in-memory budget was charged (an
uncontended lock does not suspend) while the spend ledger and the audit chain
never saw the request. Rows that were written said ``200`` and unblocked even
when a guardrail had cut the stream or the upstream had died mid-way, and the
token and cost counters, fed from the response body, saw no streaming traffic.

These run the real forwarder against a local upstream.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiohttp
import anyio
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from prometheus_client import REGISTRY

from core.circuit_breaker import CircuitManager
from proxy import forwarder as forwarder_module
from proxy.forwarder import RequestForwarder

CHUNK = b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n'
USAGE = b'data: {"usage":{"prompt_tokens":5,"completion_tokens":7}}\n\n'
DONE = b"data: [DONE]\n\n"


def _sample(name, **labels):
    return REGISTRY.get_sample_value(name, labels) or 0.0


@pytest.fixture
async def upstream():
    """An SSE upstream whose behaviour is chosen per test."""
    behaviour = {"mode": "complete"}
    started: list[TestServer] = []

    async def handler(request):
        resp = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        await resp.write(CHUNK)
        mode = behaviour["mode"]
        if mode == "complete":
            await resp.write(USAGE)
            await resp.write(DONE)
            await resp.write_eof()
        elif mode == "hang":
            await asyncio.sleep(30)
        elif mode == "die":
            request.transport.close()  # connection drops mid-body
        return resp

    app = web.Application()
    app.router.add_route("POST", "/{tail:.*}", handler)
    server = TestServer(app)
    await server.start_server()
    started.append(server)
    yield str(server.make_url("")).rstrip("/"), behaviour
    await server.close()


class Store:
    def __init__(self):
        self.spend, self.audit = [], []

    # A real store awaits I/O. Without a suspension point here a cancelled
    # caller would sail through these writes and hide the bug under test.
    async def log_spend(self, **kw):
        await asyncio.sleep(0)
        self.spend.append(kw)

    async def log_audit(self, **kw):
        await asyncio.sleep(0)
        self.audit.append(kw)


def _setup(store, security=None):
    rotator = SimpleNamespace(
        total_cost_today=0.0,
        _budget_date=None,
        config={"budget": {"daily_limit": 50.0}},
        enqueue_write=lambda k, v: None,
    )
    fwd = RequestForwarder(
        config={},
        circuit_manager=CircuitManager(),
        budget_lock=asyncio.Lock(),
        get_session=AsyncMock(),
        add_log=AsyncMock(),
        security=security,
    )
    ctx = SimpleNamespace(
        body={
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "hello"}],
            "stream": True,
        },
        metadata={"_key_prefix": "sk-test", "req_id": "req-1", "duration": 0.1},
        response=None,
        session_id="sess",
        state=SimpleNamespace(extra={"store": store}),
    )
    cost_ref = {"_budget_lock": fwd._budget_lock, "_rotator": rotator}
    return fwd, ctx, cost_ref, rotator


async def _forward(fwd, ctx, cost_ref, base):
    target = SimpleNamespace(id="ep", url=base, provider="openai", provider_type="openai")
    async with aiohttp.ClientSession() as session:
        return await fwd.forward_with_fallback(ctx, target, {}, session, cost_ref)


async def _drain_finalizers():
    pending = list(forwarder_module._STREAM_FINALIZERS)
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


async def test_a_completed_stream_is_charged_logged_and_counted(upstream):
    base, _ = upstream
    store = Store()
    fwd, ctx, cost_ref, rotator = _setup(store)
    before = _sample("llm_proxy_token_usage_total", endpoint="/v1/chat/completions", role="prompt")
    done_before = _sample("llm_proxy_stream_outcomes_total", outcome="completed")

    async with aiohttp.ClientSession() as session:
        target = SimpleNamespace(id="ep", url=base, provider="openai", provider_type="openai")
        response = await fwd.forward_with_fallback(ctx, target, {}, session, cost_ref)
        body = b"".join([c async for c in response.body_iterator])
    await _drain_finalizers()

    assert body == CHUNK + USAGE + DONE
    assert [r["status"] for r in store.spend] == [200]
    assert store.audit[0]["status"] == 200 and store.audit[0]["blocked"] is False
    assert rotator.total_cost_today > 0
    assert _sample("llm_proxy_stream_outcomes_total", outcome="completed") == done_before + 1
    assert (
        _sample("llm_proxy_token_usage_total", endpoint="/v1/chat/completions", role="prompt")
        == before + 5
    )


async def test_a_client_that_disconnects_still_leaves_its_rows(upstream):
    base, behaviour = upstream
    behaviour["mode"] = "hang"
    store = Store()
    fwd, ctx, cost_ref, rotator = _setup(store)
    gone_before = _sample("llm_proxy_stream_outcomes_total", outcome="client_disconnect")

    async with aiohttp.ClientSession() as session:
        target = SimpleNamespace(id="ep", url=base, provider="openai", provider_type="openai")
        response = await fwd.forward_with_fallback(ctx, target, {}, session, cost_ref)

        # Starlette stops a response on disconnect with an anyio cancel scope,
        # which re-delivers the cancellation at every await until the scope
        # exits (a bare task.cancel() is delivered once and understates this).
        with anyio.CancelScope() as scope:

            async def stop_soon():
                await asyncio.sleep(0.3)  # first chunk delivered, upstream silent
                scope.cancel()

            stopper = asyncio.create_task(stop_soon())
            async for _ in response.body_iterator:
                pass
        await stopper
    await _drain_finalizers()

    assert len(store.spend) == 1 and len(store.audit) == 1
    assert store.audit[0]["status"] == 499
    assert store.audit[0]["block_reason"] == "client_disconnect"
    assert (
        _sample("llm_proxy_stream_outcomes_total", outcome="client_disconnect")
        == gone_before + 1
    )


async def test_a_stream_cut_by_a_guardrail_is_recorded_as_blocked(upstream):
    base, _ = upstream
    store = Store()

    class Shield:
        async def analyze_speculative(self, prompt, chunks, kill_event):
            kill_event.set()  # the guardrail fires at once

    fwd, ctx, cost_ref, _ = _setup(store, security=Shield())
    blocked_before = _sample("llm_proxy_stream_outcomes_total", outcome="blocked")

    response = await _forward(fwd, ctx, cost_ref, base)
    body = b"".join([c async for c in response.body_iterator])
    await _drain_finalizers()

    assert b"stream_blocked" in body
    assert store.audit[0]["blocked"] is True
    assert store.audit[0]["block_reason"] == "stream_blocked"
    assert _sample("llm_proxy_stream_outcomes_total", outcome="blocked") == blocked_before + 1


async def test_an_upstream_that_dies_mid_stream_is_not_recorded_as_a_200(upstream):
    base, behaviour = upstream
    behaviour["mode"] = "die"
    store = Store()
    fwd, ctx, cost_ref, _ = _setup(store)
    failed_before = _sample("llm_proxy_stream_outcomes_total", outcome="upstream_error")

    response = await _forward(fwd, ctx, cost_ref, base)
    with pytest.raises((aiohttp.ClientError, OSError, TimeoutError, RuntimeError)):
        async for _ in response.body_iterator:
            pass
    await _drain_finalizers()

    assert store.audit[0]["status"] == 502
    assert store.audit[0]["block_reason"] == "upstream_error"
    assert (
        _sample("llm_proxy_stream_outcomes_total", outcome="upstream_error")
        == failed_before + 1
    )

"""Streams read as events, a breaker that recovers, and a timeout that does not bill twice.

Four defects, each run here against the real forwarder and a local upstream:

* a stream was parsed one TCP read at a time, so an event cut by a read was
  dropped: words missing from the answer, or the usage record lost;
* without a usage record the completion was estimated from the SSE framing
  rather than the text (40 to 100 times too many tokens), and the Anthropic,
  Google and Azure streams never carried one;
* the circuit breaker's routing filter took the half-open probe, three exit
  paths never reported an outcome, and a probe never reported was held for good;
* ``server.timeout`` (30 s) capped a whole non-streaming generation, and the
  timeout sent the request to the next provider.
"""

import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from fastapi import HTTPException

from core import circuit_breaker
from core.circuit_breaker import CircuitManager, CircuitState, LocalCircuitBreaker
from core.tokenizer import count_tokens
from proxy import forwarder as forwarder_module
from proxy.adapters.anthropic import AnthropicAdapter
from proxy.adapters.azure import AzureAdapter
from proxy.adapters.google import GoogleAdapter
from proxy.adapters.sse import MAX_EVENT_BYTES, SSEReassembler
from proxy.forwarder import (
    RequestForwarder,
    UpstreamReadTimeout,
    _BoundedStreamBuffer,
    _StreamObserver,
)
from proxy.http_session import build_http_session, response_timeout

# ── whole events out of arbitrary reads ─────────────────────────────────────


def _every_split(stream: bytes):
    """The stream delivered in two reads, cut at every possible byte."""
    for cut in range(1, len(stream)):
        yield stream[:cut], stream[cut:]


def test_an_event_cut_by_a_read_comes_back_whole():
    first, second = b'data: {"a":1}\n\n', b'data: {"b":2}\n\n'
    for head, tail in _every_split(first + second):
        r = SSEReassembler()
        assert r.feed(head) + r.feed(tail) == [first, second]
        assert r.flush() == b""


def test_crlf_events_and_a_stream_that_ends_without_a_blank_line():
    r = SSEReassembler()

    assert r.feed(b"event: x\r\ndata: 1\r\n\r\ndata: 2") == [b"event: x\ndata: 1\n\n"]
    assert r.flush() == b"data: 2"
    assert r.flush() == b""


def test_an_event_that_never_ends_is_not_buffered_without_bound():
    r = SSEReassembler()

    out = r.feed(b"x" * (MAX_EVENT_BYTES + 1))

    assert len(out) == 1 and len(out[0]) == MAX_EVENT_BYTES + 1
    assert r.flush() == b""


# ── the translators, cut at every byte ──────────────────────────────────────


class _Response:
    def __init__(self, reads):
        self.status, self.content_type, self._reads = 200, "text/event-stream", reads
        self.content = self

    async def iter_any(self):
        for chunk in self._reads:
            yield chunk

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _Session:
    def __init__(self, reads):
        self._reads = reads

    def post(self, url, **kwargs):
        return _Response(self._reads)


async def _translated(adapter, reads) -> bytes:
    out = b""
    async for chunk in adapter.stream("http://x", {}, {}, _Session(reads)):
        out += chunk
    return out


def _deltas(stream: bytes) -> str:
    text = ""
    for line in stream.decode().split("\n"):
        if line.startswith("data: ") and line[6:] != "[DONE]":
            for choice in json.loads(line[6:]).get("choices", []):
                text += choice.get("delta", {}).get("content") or ""
    return text


def _usage(stream: bytes) -> dict:
    for line in stream.decode().split("\n"):
        if line.startswith("data: ") and '"usage"' in line:
            return json.loads(line[6:])["usage"]
    return {}


def _event(name, data) -> bytes:
    return f"event: {name}\ndata: {json.dumps(data)}\n\n".encode()


ANTHROPIC = b"".join(
    [
        _event("message_start", {"type": "message_start", "message": {"id": "m1", "usage": {"input_tokens": 25, "output_tokens": 1}}}),
        _event("content_block_delta", {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Hello"}}),
        _event("content_block_delta", {"type": "content_block_delta", "delta": {"type": "text_delta", "text": " wörld"}}),
        _event("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 12}}),
        _event("message_stop", {"type": "message_stop"}),
    ]
)


async def test_an_anthropic_stream_is_the_same_wherever_the_reads_fall():
    whole = await _translated(AnthropicAdapter(), [ANTHROPIC])
    assert _deltas(whole) == "Hello wörld"
    assert whole.rstrip().endswith(b"data: [DONE]")

    for head, tail in _every_split(ANTHROPIC):
        assert _deltas(await _translated(AnthropicAdapter(), [head, tail])) == "Hello wörld"


async def test_an_anthropic_stream_carries_its_usage():
    out = await _translated(AnthropicAdapter(), [ANTHROPIC])

    assert _usage(out) == {"prompt_tokens": 25, "completion_tokens": 12, "total_tokens": 37}
    assert out.index(b'"usage"') < out.index(b"[DONE]")


def _gemini(text, **extra) -> bytes:
    candidate = {"content": {"parts": [{"text": text}]}}
    candidate.update(extra.pop("candidate", {}))
    return ("data: " + json.dumps({"candidates": [candidate], **extra}) + "\r\n\r\n").encode()


GEMINI = b"".join(
    [
        _gemini("Ciao"),
        _gemini(" mondo", candidate={"finishReason": "STOP"},
                usageMetadata={"promptTokenCount": 9, "candidatesTokenCount": 4, "totalTokenCount": 13}),
    ]
)


async def test_a_gemini_stream_is_the_same_wherever_the_reads_fall_and_carries_usage():
    whole = await _translated(GoogleAdapter(), [GEMINI])
    assert _deltas(whole) == "Ciao mondo"
    assert _usage(whole) == {"prompt_tokens": 9, "completion_tokens": 4, "total_tokens": 13}

    for head, tail in _every_split(GEMINI):
        out = await _translated(GoogleAdapter(), [head, tail])
        assert _deltas(out) == "Ciao mondo" and _usage(out)["completion_tokens"] == 4


async def test_a_gemini_stream_cut_by_max_tokens_still_ends():
    """Only STOP used to end the stream: no finish_reason, no [DONE]."""
    out = await _translated(GoogleAdapter(), [_gemini("half an ans", candidate={"finishReason": "MAX_TOKENS"})])

    reasons = [
        json.loads(line[6:])["choices"][0]["finish_reason"]
        for line in out.decode().split("\n")
        if line.startswith("data: {") and json.loads(line[6:]).get("choices")
    ]
    assert reasons[-1] == "length"
    assert out.rstrip().endswith(b"data: [DONE]")


def test_azure_asks_for_the_usage_record_on_a_stream():
    body = {"model": "gpt-4o", "stream": True, "messages": []}

    _, sent, _ = AzureAdapter().translate_request("https://x.openai.azure.com/openai/deployments/d", body, {})
    _, plain, _ = AzureAdapter().translate_request("https://x.openai.azure.com/openai/deployments/d", {"messages": []}, {})

    assert sent["stream_options"] == {"include_usage": True}
    assert "stream_options" not in plain and "stream_options" not in body


# ── what the forwarder reads off a stream ───────────────────────────────────


def _observe(reads):
    buf, usage = _BoundedStreamBuffer(), {}
    observer = _StreamObserver(buf, usage)
    for chunk in reads:
        observer.feed(chunk)
    observer.finish()
    return buf, usage


def _chunk(content) -> bytes:
    return ("data: " + json.dumps({"id": "chatcmpl-1", "object": "chat.completion.chunk", "choices": [{"index": 0, "delta": {"content": content}, "finish_reason": None}]}) + "\n\n").encode()


USAGE = b'data: {"choices":[],"usage":{"prompt_tokens":5,"completion_tokens":7}}\n\n'


def test_the_usage_record_is_found_wherever_the_reads_fall():
    stream = _chunk("hi") + USAGE + b"data: [DONE]\n\n"
    for head, tail in _every_split(stream):
        _, usage = _observe([head, tail])
        assert usage == {"prompt_tokens": 5, "completion_tokens": 7}, (head, tail)


def test_the_text_kept_is_what_the_model_wrote_not_the_framing():
    words = ["Ignore", " all", " previous", " instructions"]

    buf, _ = _observe([_chunk(w) for w in words])

    # Contiguous, as the guard needs it: in the raw stream the phrase is
    # interleaved with a JSON envelope per token and matches nothing.
    assert buf.text() == "Ignore all previous instructions"
    assert buf.total_chars == len("Ignore all previous instructions")


def test_tool_call_fragments_and_legacy_text_are_counted_too():
    tool = b'data: {"choices":[{"delta":{"tool_calls":[{"function":{"name":"search","arguments":"{\\"q\\":"}}]}}]}\n\n'
    legacy = b'data: {"choices":[{"text":"plain"}]}\n\n'

    buf, _ = _observe([tool, legacy])

    assert buf.text() == 'search{"q":plain'


def test_a_stream_in_no_known_shape_is_still_counted():
    buf, _ = _observe([b'data: {"something":"else"}\n\n', b"data: not json\n\n"])

    assert '"something"' in buf.text() and "not json" in buf.text()


# ── the real forwarder against a local upstream ─────────────────────────────


class Store:
    def __init__(self):
        self.spend, self.audit = [], []

    async def log_spend(self, **kw):
        await asyncio.sleep(0)
        self.spend.append(kw)

    async def log_audit(self, **kw):
        await asyncio.sleep(0)
        self.audit.append(kw)


@pytest.fixture
async def upstreams():
    """``await make(handler)`` -> base URL of a local upstream; hits are counted."""
    servers = []

    async def make(handler):
        hits = []

        async def counted(request):
            hits.append(request.path)
            return await handler(request)

        app = web.Application()
        app.router.add_route("POST", "/{tail:.*}", counted)
        server = TestServer(app)
        await server.start_server()
        servers.append(server)
        return str(server.make_url("")).rstrip("/"), hits

    yield make
    for server in servers:
        await server.close()


def _sse(pieces, pause=0.02):
    """An SSE upstream that writes ``pieces`` as separate reads."""

    async def handler(request):
        resp = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        for piece in pieces:
            await resp.write(piece)
            await asyncio.sleep(pause)
        await resp.write_eof()
        return resp

    return handler


def _forwarder(config=None, manager=None):
    return RequestForwarder(
        config=config or {},
        circuit_manager=manager or CircuitManager(),
        budget_lock=asyncio.Lock(),
        get_session=AsyncMock(),
        add_log=AsyncMock(),
        security=None,
    )


def _ctx(store, stream=True, model="gpt-4o"):
    return SimpleNamespace(
        body={"model": model, "messages": [{"role": "user", "content": "hello"}], "stream": stream},
        metadata={"_key_prefix": "sk-test", "req_id": "req-1", "duration": 0.1},
        response=None,
        session_id="sess",
        state=SimpleNamespace(extra={"store": store}),
    )


def _cost_ref(fwd):
    rotator = SimpleNamespace(
        total_cost_today=0.0, _budget_date=None,
        config={"budget": {"daily_limit": 50.0}}, enqueue_write=lambda k, v: None,
    )
    return {"_budget_lock": fwd._budget_lock, "_rotator": rotator}


def _target(base, provider="openai", ident="ep"):
    return SimpleNamespace(id=ident, url=base, provider=provider, provider_type=provider, metadata={})


async def _stream_through(fwd, ctx, base, provider="openai"):
    async with aiohttp.ClientSession() as session:
        response = await fwd.forward_with_fallback(ctx, _target(base, provider), {}, session, _cost_ref(fwd))
        body = b"".join([c async for c in response.body_iterator])
    pending = list(forwarder_module._STREAM_FINALIZERS)
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    return body


async def test_a_stream_without_usage_is_billed_on_its_text_not_on_its_framing(upstreams):
    text = "word " * 200
    base, _ = await upstreams(_sse([_chunk("word ") * 20 for _ in range(10)] + [b"data: [DONE]\n\n"], pause=0))
    store = Store()
    fwd = _forwarder()

    await _stream_through(fwd, _ctx(store), base)

    real = count_tokens(text, "gpt-4o")
    (row,) = store.audit
    # The framing of these 200 chunks is about 26,000 characters: counted as
    # text it came to thousands of tokens.
    assert real * 0.8 <= row["completion_tokens"] <= real * 1.2, (row["completion_tokens"], real)
    assert store.spend[0]["completion_tokens"] == row["completion_tokens"]


async def test_a_usage_record_cut_by_a_read_is_still_what_gets_billed(upstreams):
    cut = len(USAGE) // 2
    base, _ = await upstreams(_sse([_chunk("hi") + USAGE[:cut], USAGE[cut:] + b"data: [DONE]\n\n"]))
    store = Store()

    body = await _stream_through(_forwarder(), _ctx(store), base)

    assert body == _chunk("hi") + USAGE + b"data: [DONE]\n\n"  # the client's bytes are untouched
    assert (store.audit[0]["prompt_tokens"], store.audit[0]["completion_tokens"]) == (5, 7)


async def test_a_streamed_claude_answer_is_whole_and_billed_on_the_reported_usage(upstreams):
    cut = ANTHROPIC.index(b"Hello") + 2  # inside a text delta
    base, _ = await upstreams(_sse([ANTHROPIC[:cut], ANTHROPIC[cut:]]))
    store = Store()

    body = await _stream_through(_forwarder(), _ctx(store, model="claude-sonnet-4"), base, provider="anthropic")

    assert _deltas(body) == "Hello wörld"
    assert (store.audit[0]["prompt_tokens"], store.audit[0]["completion_tokens"]) == (25, 12)


# ── a timeout that does not bill twice ──────────────────────────────────────


def _slow(seconds):
    async def handler(request):
        await asyncio.sleep(seconds)
        return web.json_response({"choices": [{"message": {"content": "late"}}], "usage": {}})

    return handler


async def _ok(request):
    return web.json_response({"choices": [{"message": {"content": "fallback"}}], "usage": {}})


def _chain_config(fallback_base):
    return {
        "fallback_chains": {"gpt-4o": [{"provider": "backup", "model": "gpt-4o-mini"}]},
        "endpoints": {"backup": {"provider": "backup", "base_url": fallback_base}},
    }


async def test_a_long_non_streaming_answer_is_not_cut_at_the_stream_idle_timeout(upstreams):
    """server.timeout is how long a stream may go quiet. It was also, by
    accident, the longest a non-streaming answer could take to generate."""
    base, hits = await upstreams(_slow(1.5))
    session = build_http_session({"server": {"timeout": "1s"}})
    try:
        response = await _forwarder().forward_with_fallback(_ctx(Store(), stream=False), _target(base), {}, session, {})
    finally:
        await session.close()

    assert response.status_code == 200 and len(hits) == 1


async def test_a_read_timeout_is_a_504_and_is_not_sent_to_the_next_provider(upstreams):
    slow_base, slow_hits = await upstreams(_slow(3))
    backup_base, backup_hits = await upstreams(_ok)
    manager = CircuitManager()
    fwd = _forwarder(_chain_config(backup_base), manager)
    session = build_http_session({"server": {"response_timeout": "1s"}})
    try:
        with pytest.raises(UpstreamReadTimeout) as caught:
            await fwd.forward_with_fallback(_ctx(Store(), stream=False), _target(slow_base), {}, session, {})
    finally:
        await session.close()

    assert caught.value.status_code == 504
    assert len(slow_hits) == 1 and backup_hits == []  # delivered once, billed once
    assert (await manager.get_breaker("ep")).failure_count == 1  # and the breaker heard


async def test_an_upstream_that_cannot_be_reached_still_falls_back(upstreams):
    backup_base, backup_hits = await upstreams(_ok)
    manager = CircuitManager()
    fwd = _forwarder(_chain_config(backup_base), manager)
    session = build_http_session({})
    try:
        response = await fwd.forward_with_fallback(
            _ctx(Store(), stream=False), _target("http://127.0.0.1:9"), {}, session, {}
        )
    finally:
        await session.close()

    assert response.status_code == 200 and len(backup_hits) == 1
    # Nothing was sent, so it is retried; and the dead endpoint is counted,
    # which it was not: no non-streaming network error ever reached the breaker.
    assert (await manager.get_breaker("ep")).failure_count == 1


async def test_the_response_timeout_is_per_request_and_absent_for_a_foreign_session():
    session = build_http_session({"server": {"timeout": "30s", "response_timeout": "120s"}})
    default = build_http_session({})
    try:
        assert response_timeout(session)["timeout"].sock_read == 120
        assert session.timeout.sock_read == 30  # streams keep the idle bound
        assert response_timeout(default)["timeout"].sock_read == 600
        assert response_timeout(object()) == {} and response_timeout(_Session([])) == {}
    finally:
        await session.close()
        await default.close()


# ── a breaker that recovers ─────────────────────────────────────────────────


async def _opened(manager, name="ep", recovery=60):
    manager.recovery_timeout = recovery
    breaker = await manager.get_breaker(name)
    for _ in range(breaker.failure_threshold):
        await breaker.report_failure()
    assert breaker.state == CircuitState.OPEN
    return breaker


async def test_asking_which_endpoints_are_usable_does_not_take_the_probe():
    manager = CircuitManager()
    breaker = await _opened(manager)
    breaker.last_failure_time = time.time() - 61  # due for a probe

    # Routing, /health and the dashboard all ask; none of them is a request.
    for _ in range(3):
        assert await manager.filter_executable(["ep"]) == {"ep"}
    assert breaker.state == CircuitState.OPEN

    # So the request that follows is the probe, and its success closes the circuit.
    assert await breaker.can_execute() is True
    await breaker.report_success()
    assert breaker.state == CircuitState.CLOSED


async def test_an_open_circuit_that_is_not_due_is_filtered_out():
    manager = CircuitManager()
    await _opened(manager)

    assert await manager.filter_executable(["ep", "other"]) == {"other"}


async def test_a_probe_whose_outcome_never_comes_is_given_up_after_one_recovery_period():
    breaker = LocalCircuitBreaker(name="ep", failure_threshold=1, recovery_timeout=60)
    await breaker.report_failure()
    breaker.last_failure_time = time.time() - 61
    assert await breaker.can_execute() is True  # the probe
    assert await breaker.can_execute() is False  # one at a time
    assert breaker.would_admit() is False

    breaker._probe_started = time.time() - 61  # the caller vanished; nothing was reported

    assert breaker.would_admit() is True
    assert await breaker.can_execute() is True


async def test_a_stream_answered_with_a_client_error_gives_the_probe_back(upstreams):
    async def unauthorized(request):
        return web.json_response({"error": "bad key"}, status=401)

    base, _ = await upstreams(unauthorized)
    manager = CircuitManager()
    breaker = await _opened(manager)
    breaker.last_failure_time = time.time() - 61
    fwd = _forwarder(manager=manager)

    async with aiohttp.ClientSession() as session:
        response = await fwd.forward_with_fallback(_ctx(Store()), _target(base), {}, session, {})

    assert response.status_code == 401  # the caller's answer, relayed
    assert breaker.state == CircuitState.CLOSED  # the endpoint answered


async def test_a_dead_endpoint_opens_its_circuit_from_non_streaming_traffic():
    manager = CircuitManager()
    fwd = _forwarder(manager=manager)
    session = build_http_session({})
    try:
        for _ in range(manager.failure_threshold):
            with pytest.raises((aiohttp.ClientError, OSError, HTTPException)):
                await fwd.forward_with_fallback(
                    _ctx(Store(), stream=False), _target("http://127.0.0.1:9"), {}, session, {}
                )
    finally:
        await session.close()

    assert (await manager.get_breaker("ep")).state == CircuitState.OPEN


def test_the_shared_probe_expires_too():
    """In Redis the probe key had no TTL: a leaked probe outlived the process."""
    assert circuit_breaker.LUA_CHECK_SCRIPT.count("'EX'") == 2

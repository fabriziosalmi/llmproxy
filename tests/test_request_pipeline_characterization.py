"""What process_proxy_request does, stage by stage, pinned before it was split up.

The function is the most executed one in the proxy and had a cyclomatic complexity of
38. Splitting it is only safe if what it does is written down first, so this drives
the real function through every exit it has and asserts the observable behaviour:
the order the rings run in, which status each stop produces, what is charged and
recorded, the headers on the response, and what happens on an unexpected failure.
These were run green on the single-function version before it was refactored.
"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse
from starlette.responses import JSONResponse

from core.log_context import get_request_id
from core.plugin_engine import PluginHook, PluginState
from proxy import request_pipeline
from proxy.request_pipeline import process_proxy_request


class Harness:
    """A mock orchestrator that records what the pipeline asks of it."""

    def __init__(self, monkeypatch, *, spent=0.0, config=None):
        self.events: list[tuple] = []
        self.spawned: list = []
        self.audited: list[dict] = []
        self.ring_effects: dict[str, callable] = {}
        self.stats: list[tuple] = []

        o = MagicMock()
        o.config = config if config is not None else {"budget": {"daily_limit": 50.0}}
        o.total_cost_today = spent
        o._budget_date = None
        o._budget_lock = asyncio.Lock()
        o._add_log = AsyncMock()
        o._get_session = AsyncMock(return_value="SESSION")
        o.enqueue_write = MagicMock()
        o.redis_client = None
        o.plugin_state = PluginState(cache=None, metrics=MagicMock(), config={}, extra={})
        o.negative_cache.check = MagicMock(return_value=None)
        o.security.inspect = AsyncMock(return_value=None)
        o.zt_manager.get_identity_headers = MagicMock(return_value={"X-ZT": "1"})
        o.webhooks.dispatch = AsyncMock()
        o.response_signer.enabled = False
        o.cache_backend._enabled = True
        o.cache_backend.put = AsyncMock()
        o.plugin_manager.annotate_ring_trace = MagicMock()

        async def log_audit(**row):
            self.audited.append(row)

        o.store.log_audit = AsyncMock(side_effect=log_audit)

        async def execute_ring(hook, ctx):
            self.events.append(("ring", hook.value))
            effect = self.ring_effects.get(hook.value)
            if effect:
                effect(ctx)

        o.plugin_manager.execute_ring = AsyncMock(side_effect=execute_ring)

        self.forward_response = JSONResponse({"choices": [{"message": {"content": "ok"}}]})
        self.forward_delta = 0.0
        self.forward_args = None

        async def forward(ctx, target, headers, session, cost_ref=None):
            self.events.append(("forward",))
            self.forward_args = SimpleNamespace(
                model=ctx.body.get("model"), headers=dict(headers), session=session, target=target
            )
            cost_ref["delta"] = self.forward_delta
            ctx.response = self.forward_response
            return ctx.response

        o.forwarder.forward_with_fallback = AsyncMock(side_effect=forward)

        def spawn(coro):
            self.spawned.append(coro)
            return MagicMock()

        o._spawn_task = spawn
        self.o = o

        self.metrics = MagicMock()
        monkeypatch.setattr(request_pipeline, "MetricsTracker", self.metrics)

        async def record_stats(endpoint_id, latency_ms, success, **kw):
            self.stats.append((endpoint_id, latency_ms, success))

        monkeypatch.setattr(request_pipeline, "update_endpoint_stats", record_stats)

    def request(self, content="hello", **body_extra):
        request = MagicMock(spec=Request)
        request.headers = {}
        request.client = SimpleNamespace(host="203.0.113.9")
        request.state = SimpleNamespace(quota_exceeded=False)
        request.url = SimpleNamespace(path="/v1/chat/completions")
        body = {"model": "gpt-4o", "messages": [{"role": "user", "content": content}], **body_extra}
        request.json = AsyncMock(return_value=body)
        return request

    async def run(self, content="hello", session_id="sess-1234567890", **body_extra):
        return await process_proxy_request(self.o, self.request(content, **body_extra), None, session_id)

    async def run_spawned(self):
        for coro in self.spawned:
            await coro
        self.spawned.clear()


@pytest.fixture
def h(monkeypatch):
    harness = Harness(monkeypatch)
    yield harness
    for coro in harness.spawned:  # background rings a test chose not to run
        coro.close()


def rings(h):
    return [e[1] for e in h.events if e[0] == "ring"]


# ── the happy path ────────────────────────────────────────────────────────────


async def test_a_normal_request_runs_the_rings_in_order_and_returns_the_upstream_response(h):
    response = await h.run()

    assert response is h.forward_response
    assert [e[0] if e[0] == "forward" else e[1] for e in h.events] == [
        "ingress", "pre_flight", "routing", "forward", "post_flight",
    ]
    await h.run_spawned()
    assert rings(h)[-1] == "background"
    latencies = [c.args[0] for c in h.metrics.track_ring_latency.call_args_list]
    assert latencies == ["ingress", "pre_flight", "routing", "post_flight", "background"]


async def test_the_forwarder_gets_the_session_the_identity_headers_and_the_request_body(h):
    await h.run()

    assert h.forward_args.session == "SESSION"
    assert h.forward_args.headers == {"X-ZT": "1"}
    assert h.forward_args.model == "gpt-4o"


async def test_a_model_alias_is_resolved_before_routing_and_remembered(h):
    h.o.config = {"budget": {"daily_limit": 50.0}, "model_aliases": {"fast": "gpt-4o-mini"}}
    seen = {}
    h.ring_effects["routing"] = lambda ctx: seen.update(model=ctx.body["model"], alias=ctx.metadata.get("_model_alias"))

    await h.run(model="fast")

    assert seen == {"model": "gpt-4o-mini", "alias": "fast"}
    assert h.forward_args.model == "gpt-4o-mini"


async def test_a_non_streaming_response_is_charged_and_the_total_persisted(h):
    h.forward_delta = 0.25

    await h.run()

    assert h.o.total_cost_today == pytest.approx(0.25)
    h.o.enqueue_write.assert_any_call("budget:daily_total", pytest.approx(0.25))


async def test_a_streaming_response_is_not_charged_here(h):
    h.forward_delta = 0.25
    h.forward_response = StreamingResponse(iter([b"x"]), media_type="text/event-stream")

    await h.run(stream=True)

    assert h.o.total_cost_today == 0.0  # the stream generator charges when it ends


async def test_endpoint_stats_are_recorded_with_the_outcome(h):
    h.o.plugin_manager.execute_ring.side_effect = None
    target = SimpleNamespace(id="ep-1")

    async def ring(hook, ctx):
        h.events.append(("ring", hook.value))
        if hook == PluginHook.ROUTING:
            ctx.metadata["target_endpoint"] = target

    h.o.plugin_manager.execute_ring = AsyncMock(side_effect=ring)

    await h.run()

    assert [(s[0], s[2]) for s in h.stats] == [("ep-1", True)]


async def test_an_error_status_from_upstream_is_recorded_as_a_failure(h):
    h.forward_response = JSONResponse({"error": "x"}, status_code=500)

    await h.run()

    assert h.stats[0][2] is False


async def test_proxy_headers_are_set_on_the_response(h):
    h.ring_effects["routing"] = lambda ctx: ctx.metadata.update(
        _provider="openai", _cache_status="MISS", _budget_downgrade_headers={"X-Downgrade": "gpt-4o-mini"}
    )

    response = await h.run()

    assert response.headers["X-LLMProxy-Provider"] == "openai"
    assert response.headers["X-LLMProxy-Cache"] == "MISS"
    assert response.headers["X-Downgrade"] == "gpt-4o-mini"
    assert len(response.headers["X-LLMProxy-Request-Id"]) == 16


async def test_the_response_is_signed_when_signing_is_on(h):
    h.o.response_signer.enabled = True
    h.o.response_signer.sign_response = MagicMock(return_value={"X-Sig": "abc"})

    response = await h.run()

    assert response.headers["X-Sig"] == "abc"
    kwargs = h.o.response_signer.sign_response.call_args.kwargs
    assert kwargs["model"] == "gpt-4o" and kwargs["response_body"] == response.body


async def test_the_ring_trace_is_annotated_with_timings(h):
    await h.run()

    (req_id,), fields = h.o.plugin_manager.annotate_ring_trace.call_args
    assert len(req_id) == 16 and set(fields) >= {"total_ms", "upstream_ms"}


async def test_the_request_id_is_unbound_afterwards_on_success_and_failure(h):
    before = get_request_id()
    await h.run()
    assert get_request_id() == before

    h.o.forwarder.forward_with_fallback = AsyncMock(side_effect=RuntimeError("boom"))
    with pytest.raises(HTTPException):
        await h.run()
    assert get_request_id() == before


# ── the stops ─────────────────────────────────────────────────────────────────


async def test_a_negative_cache_hit_is_a_403_before_anything_else(h):
    h.o.negative_cache.check = MagicMock(return_value="seen before")

    with pytest.raises(HTTPException) as caught:
        await h.run()

    assert (caught.value.status_code, caught.value.detail) == (403, "seen before")
    assert h.events == []
    h.o.security.inspect.assert_not_awaited()
    h.metrics.track_injection_blocked.assert_called_once()


async def test_a_shield_block_is_a_403_and_is_remembered(h):
    h.o.security.inspect = AsyncMock(return_value="injection detected")

    with pytest.raises(HTTPException) as caught:
        await h.run(session_id="sess-1234567890")

    assert (caught.value.status_code, caught.value.detail) == (403, "injection detected")
    assert h.events == []
    h.o.negative_cache.add.assert_called_once()
    kwargs = h.o.security.inspect.call_args.kwargs
    assert kwargs == {"ip": "203.0.113.9", "key_prefix": "sess-123"}


async def test_the_default_session_has_no_key_prefix(h):
    await h.run(session_id="default")

    assert h.o.security.inspect.call_args.kwargs["key_prefix"] == ""


async def test_an_ingress_block_is_a_403_with_a_webhook(h):
    def block(ctx):
        ctx.stop_chain, ctx.error = True, "no key"

    h.ring_effects["ingress"] = block

    with pytest.raises(HTTPException) as caught:
        await h.run()

    assert (caught.value.status_code, caught.value.detail) == (403, "no key")
    assert rings(h) == ["ingress"]
    assert len(h.spawned) == 2  # the INJECTION_BLOCKED webhook and the audit row
    await h.run_spawned()
    h.o.webhooks.dispatch.assert_awaited_once()


async def test_a_pre_flight_block_uses_the_plugins_status(h):
    def block(ctx):
        ctx.stop_chain, ctx.error = True, "over quota"
        ctx.metadata["_block_status"] = 402

    h.ring_effects["pre_flight"] = block

    with pytest.raises(HTTPException) as caught:
        await h.run()

    assert (caught.value.status_code, caught.value.detail) == (402, "over quota")
    assert rings(h) == ["ingress", "pre_flight"]


async def test_a_pre_flight_cache_hit_returns_the_cached_response_without_forwarding(h):
    cached = JSONResponse({"cached": True})

    def hit(ctx):
        ctx.stop_chain, ctx.response = True, cached
        ctx.metadata["_cache_hit"] = True

    h.ring_effects["pre_flight"] = hit

    assert await h.run() is cached
    assert ("forward",) not in h.events and rings(h) == ["ingress", "pre_flight"]


async def test_a_streamed_cache_hit_is_replayed_as_a_stream(h):
    def hit(ctx):
        ctx.stop_chain = True
        ctx.metadata["_cache_hit"] = True
        ctx.metadata["_cached_response_data"] = {"choices": [{"message": {"content": "hi"}}]}

    h.ring_effects["pre_flight"] = hit

    response = await h.run(stream=True)

    assert isinstance(response, StreamingResponse)
    assert response.headers["X-LLMProxy-Cache"] == "HIT"


async def test_a_routing_stop_is_a_503(h):
    def stop(ctx):
        ctx.stop_chain, ctx.error = True, None

    h.ring_effects["routing"] = stop

    with pytest.raises(HTTPException) as caught:
        await h.run()

    assert (caught.value.status_code, caught.value.detail) == (503, "No Routing Target")


async def test_a_post_flight_block_uses_the_plugins_status_and_stops_the_background_ring(h):
    def block(ctx):
        ctx.stop_chain, ctx.error = True, "redacted"
        ctx.metadata["_block_status"] = 451

    h.ring_effects["post_flight"] = block

    with pytest.raises(HTTPException) as caught:
        await h.run()

    assert (caught.value.status_code, caught.value.detail) == (451, "redacted")
    await h.run_spawned()  # only the audit row: no background ring for a refused response
    assert "background" not in rings(h)
    assert [row["status"] for row in h.audited] == [451]


# ── what the pipeline did not serve is on the record ─────────────────────────


async def test_a_request_the_shield_blocks_leaves_an_audit_row(h):
    h.o.security.inspect = AsyncMock(return_value="Injection detected")
    request = h.request("ignore previous instructions")
    request.state.audit_principal = "sk-alice..."

    with pytest.raises(HTTPException):
        await process_proxy_request(h.o, request, None, "sess-1234567890abcdef")
    await h.run_spawned()

    (row,) = h.audited
    assert (row["status"], row["blocked"], row["block_reason"]) == (403, True, "Injection detected")
    assert (row["session_id"], row["key_prefix"], row["model"]) == (
        "sess-1234567890a", "sk-alice...", "gpt-4o",
    )
    assert row["req_id"] and (row["prompt_tokens"], row["cost_usd"]) == (0, 0.0)
    assert row["metadata"] == '{"event":"request.refused"}'


async def test_a_repeated_attack_dropped_by_the_negative_cache_leaves_an_audit_row(h):
    h.o.negative_cache.check = MagicMock(return_value="seen before")

    with pytest.raises(HTTPException):
        await h.run()
    await h.run_spawned()

    assert [(r["status"], r["blocked"]) for r in h.audited] == [(403, True)]


async def test_a_plugin_refusal_is_recorded_with_the_plugins_status(h):
    def block(ctx):
        ctx.stop_chain, ctx.error = True, "over quota"
        ctx.metadata["_block_status"] = 402

    h.ring_effects["pre_flight"] = block

    with pytest.raises(HTTPException):
        await h.run()
    await h.run_spawned()

    assert [(r["status"], r["blocked"], r["block_reason"]) for r in h.audited] == [
        (402, True, "over quota")
    ]


async def test_a_request_that_fails_upstream_is_recorded_as_failed_not_blocked(h):
    h.o.forwarder.forward_with_fallback = AsyncMock(side_effect=RuntimeError("boom"))

    with pytest.raises(HTTPException) as caught:
        await h.run()
    await h.run_spawned()

    assert caught.value.status_code == 502
    (row,) = h.audited
    assert (row["status"], row["blocked"]) == (502, False)
    assert row["metadata"] == '{"event":"request.failed"}'
    assert "boom" not in row["block_reason"]  # the caller's text, not the exception


async def test_a_served_request_is_not_recorded_here(h):
    """The route (or the forwarder, for a stream) writes that row, with the usage."""
    await h.run()
    await h.run_spawned()

    assert h.audited == []


async def test_a_refusal_reason_is_bounded(h):
    h.o.security.inspect = AsyncMock(return_value="x" * 5000)

    with pytest.raises(HTTPException):
        await h.run()
    await h.run_spawned()

    assert len(h.audited[0]["block_reason"]) == 500


async def test_a_store_that_cannot_write_does_not_change_the_refusal(h):
    h.o.security.inspect = AsyncMock(return_value="Injection detected")
    h.o.store.log_audit = AsyncMock(side_effect=OSError("disk full"))

    with pytest.raises(HTTPException) as caught:
        await h.run()
    await h.run_spawned()

    assert caught.value.status_code == 403


async def test_the_caller_is_recorded_for_the_forwarder_to_account_a_stream_to(h):
    request = h.request()
    request.state.audit_principal = "alice@example.com"

    await process_proxy_request(h.o, request, None, "sess-1234567890")

    assert h.forward_args is not None
    assert h.o.forwarder.forward_with_fallback.call_args.args[0].metadata["_key_prefix"] == (
        "alice@example.com"
    )


async def test_an_http_error_from_the_forwarder_passes_through_unchanged(h):
    h.o.forwarder.forward_with_fallback = AsyncMock(side_effect=HTTPException(status_code=429, detail="slow down"))

    with pytest.raises(HTTPException) as caught:
        await h.run()

    assert (caught.value.status_code, caught.value.detail) == (429, "slow down")


async def test_any_other_failure_is_a_502_and_is_logged(h):
    h.o.forwarder.forward_with_fallback = AsyncMock(side_effect=KeyError("secret detail"))

    with pytest.raises(HTTPException) as caught:
        await h.run()

    assert (caught.value.status_code, caught.value.detail) == (502, "Upstream request failed")
    assert "secret detail" not in str(caught.value.detail)
    h.o.logger.error.assert_called_once()


# ── the budget verdict ────────────────────────────────────────────────────────


async def test_crossing_the_daily_limit_flags_the_request_and_says_so(h):
    h.o.total_cost_today = 49.9999
    seen = {}
    h.ring_effects["routing"] = lambda ctx: seen.update(saturated=ctx.metadata.get("_budget_saturated"))

    await h.run("A" * 4_000_000)

    assert seen["saturated"] is True
    assert "BUDGET SATURATED (Global)" in str(h.o._add_log.call_args_list)


async def test_a_per_app_quota_flags_the_request_with_its_own_message(h):
    request = h.request()
    request.state.quota_exceeded = True
    seen = {}
    h.ring_effects["routing"] = lambda ctx: seen.update(saturated=ctx.metadata.get("_budget_saturated"))

    await process_proxy_request(h.o, request, None, "sess-1234567890")

    assert seen["saturated"] is True
    assert "BUDGET SATURATED (App Quota)" in str(h.o._add_log.call_args_list)


async def test_inside_the_limit_nothing_is_flagged(h):
    seen = {}
    h.ring_effects["routing"] = lambda ctx: seen.update(saturated=ctx.metadata.get("_budget_saturated"))

    await h.run()

    assert seen["saturated"] is None


# ── the background ring ───────────────────────────────────────────────────────


async def test_a_cacheable_response_is_written_to_the_cache_in_the_background(h):
    h.ring_effects["routing"] = lambda ctx: ctx.metadata.update(_cache_key="k1", _cache_tenant="t1")

    await h.run()
    await h.run_spawned()

    kwargs = h.o.cache_backend.put.call_args.kwargs
    assert kwargs["tenant_id"] == "t1" and kwargs["model"] == "gpt-4o"
    assert kwargs["response_data"]["choices"][0]["message"]["content"] == "ok"


async def test_a_response_carrying_a_security_marker_is_not_cached(h):
    h.forward_response = JSONResponse({"choices": [{"message": {"content": "x [SEC_ERR: pii] y"}}]})
    h.ring_effects["routing"] = lambda ctx: ctx.metadata.update(_cache_key="k1")

    await h.run()
    await h.run_spawned()

    h.o.cache_backend.put.assert_not_awaited()


async def test_a_cache_bypass_or_a_disabled_cache_writes_nothing(h):
    h.ring_effects["routing"] = lambda ctx: ctx.metadata.update(_cache_key="k1", _cache_bypass=True)
    await h.run()
    await h.run_spawned()
    h.o.cache_backend.put.assert_not_awaited()

    h.ring_effects["routing"] = lambda ctx: ctx.metadata.update(_cache_key="k1")
    h.o.cache_backend._enabled = False
    await h.run()
    await h.run_spawned()
    h.o.cache_backend.put.assert_not_awaited()


async def test_a_failing_cache_write_does_not_fail_the_request(h):
    h.o.cache_backend.put = AsyncMock(side_effect=RuntimeError("redis down"))
    h.ring_effects["routing"] = lambda ctx: ctx.metadata.update(_cache_key="k1")

    response = await h.run()
    await h.run_spawned()  # must not raise

    assert response is h.forward_response


def test_the_json_import_is_still_used_by_the_background_ring():
    # json.loads(ctx.response.body) is how the cacheable text is read.
    assert json.loads(JSONResponse({"a": 1}).body) == {"a": 1}

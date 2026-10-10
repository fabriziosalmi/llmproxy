"""LLMProxy — Core request pipeline.

The 5-ring plugin pipeline that every proxied request flows through:

  L1. Negative cache (drop repeated attacks pre-pipeline)
  Pre. SecurityShield (injection / trajectory / cross-session)
  R1. INGRESS     — auth, zero-trust, rate limit
  R2. PRE_FLIGHT  — PII masking, budget guard, loop breaker, cache lookup
       (after R2: model alias / group resolve, budget downgrade)
  R3. ROUTING     — endpoint selection
       (forward upstream with cross-provider fallback)
  R4. POST_FLIGHT — sanitization, watermarking
  R5. BACKGROUND  — telemetry, export, cache write (fire-and-forget)
       (response header injection + cryptographic signing)

Extracted from proxy/rotator.py — the orchestrator now owns wiring +
lifecycle, this module owns dispatch.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any

from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from core.endpoint_stats import update_endpoint_stats
from core.log_context import reset_request_id, set_request_id
from core.metrics import MetricsTracker
from core.model_resolver import resolve_model
from core.plugin_engine import PluginContext, PluginHook
from core.pricing import estimate_cost
from core.stream_faker import fake_stream
from core.tokenizer import count_messages_tokens
from core.tool_policy import ToolPolicy, called_tools, follows_tool_result
from core.tracing import TraceManager
from core.webhooks import EventType
from proxy.audit_backlog import submit as submit_audit
from proxy.auth_helpers import audit_principal
from proxy.budget import charge_and_persist, roll_over_if_new_day

logger = logging.getLogger("llmproxy.request_pipeline")


async def process_proxy_request(
    orchestrator: Any,
    request: Any,
    body: dict[str, Any] | None = None,
    session_id: str = "default",
):
    """Run a request through the 5-ring pipeline.

    `orchestrator` is the live ProxyOrchestrator — every subsystem is
    read off it (security, plugin_manager, forwarder, cache_backend,
    response_signer, webhooks, zt_manager, …). This keeps coupling tight
    while letting the dispatch logic live in its own file.

    This function only sequences the stages; each stage is a function below that
    does one thing and either returns or raises the HTTPException that is the
    stop. It was one 330-line function (cyclomatic complexity 38).
    tests/test_request_pipeline_characterization.py pins what each stage does.
    """
    start_total = time.time()
    # The kill switch (POST /api/v1/panic, /api/v1/proxy/toggle) was checked by
    # the chat route alone: with the proxy "stopped", /v1/completions ran this
    # same pipeline against the same models. ``is False`` because the attribute
    # is absent on the orchestrators tests build.
    if getattr(orchestrator, "proxy_enabled", True) is False:
        raise HTTPException(status_code=503, detail="Proxy service is currently STOPPED.")
    if body is None:
        body = await request.json()

    # Bind the identifier before anything can log, so every record emitted for
    # this request — including the security-shield block below, and anything a
    # plugin logs from inside a ring — carries it. asyncio copies the context
    # into tasks at creation, so a background task spawned from this request
    # keeps this request's id rather than whatever ran last.
    req_id = uuid.uuid4().hex[:16]
    req_id_token = set_request_id(req_id)
    ctx = _new_context(orchestrator, request, body, session_id, req_id)

    try:
        await _screen(orchestrator, ctx, request, session_id)
        await _ingress(orchestrator, ctx, session_id)

        cached = await _pre_flight(orchestrator, ctx)
        if cached is not _CONTINUE:
            # A cached answer is an answer: it does not skip the tool policy.
            await _tool_policy(orchestrator, ctx)
            return cached

        _resolve_model(orchestrator, ctx)
        await _flag_budget(orchestrator, ctx, request, body)
        await _route(orchestrator, ctx)
        await _forward(orchestrator, ctx)
        await _tool_policy(orchestrator, ctx)
        await _post_flight(orchestrator, ctx)

        orchestrator._spawn_task(_background_ring(orchestrator, ctx))
        _decorate_response(orchestrator, ctx)
        _annotate_trace(orchestrator, ctx, start_total)
        return ctx.response

    except HTTPException as stop:
        await _audit_refusal(orchestrator, ctx, stop.status_code, stop.detail, start_total)
        raise
    except Exception as e:
        orchestrator.logger.error(f"Proxy pipeline error: {e}")
        TraceManager.capture_exception(e)
        await _audit_refusal(orchestrator, ctx, 502, "Upstream request failed", start_total)
        raise HTTPException(status_code=502, detail="Upstream request failed") from e
    finally:
        # Unbind on every exit path, including the two raises above. Without
        # this the identifier would leak into whatever the event loop runs
        # next on the same context and label unrelated records with it, which
        # is worse than having no identifier at all.
        reset_request_id(req_id_token)


#: Returned by _pre_flight when the request carries on (None is a valid answer:
#: a cache hit that produced no response).
_CONTINUE = object()

def _served_cost(ctx: PluginContext) -> float:
    """What a non-streaming response cost, from the usage the upstream reported.

    Priced as the chat route prices the same response for its spend row. An
    upstream error is not billed; a response without usage is priced on the
    prompt it was sent.
    """
    response = ctx.response
    raw = getattr(response, "body", None)
    if not raw or getattr(response, "status_code", 200) >= 400:
        return 0.0
    try:
        usage = json.loads(raw).get("usage") or {}
    except (ValueError, AttributeError, UnicodeDecodeError):
        return 0.0
    model = str(ctx.body.get("model", "") or "")
    try:
        prompt = int(usage.get("prompt_tokens") or 0) or count_messages_tokens(
            ctx.body.get("messages", []), model
        )
        return float(estimate_cost(model, prompt, int(usage.get("completion_tokens") or 0)))
    except (TypeError, ValueError):
        return 0.0


#: Longest refusal reason kept in an audit row. The reason is the text the
#: caller was sent; a plugin can put anything there.
_REASON_MAX = 500


async def _audit_refusal(
    orchestrator: Any, ctx: PluginContext, status: int, detail: Any, started: float
) -> None:
    """Record a request that the pipeline did not serve.

    The audit log only ever received a row from the code that runs after a
    response exists, so it held the requests the gateway let through and none
    of the ones it stopped: an injection blocked by the shield, a request
    refused by a plugin, a budget stop and an upstream failure all left the
    chain exactly as it was. For a security gateway that is the half of the
    record an auditor asks for first.

    A 4xx is the gateway refusing (``blocked``); a 5xx is the request failing.
    Either way the request never produced a response, so tokens and cost are 0.
    """
    store = getattr(orchestrator, "store", None)
    if store is None or not hasattr(store, "log_audit"):
        return
    refused = 400 <= status < 500
    reason = (detail if isinstance(detail, str) else json.dumps(detail, default=str))[:_REASON_MAX]
    row = {
        "ts": int(time.time()),
        "req_id": str(ctx.metadata.get("req_id", "")),
        "session_id": (ctx.session_id or "")[:16],
        "key_prefix": str(ctx.metadata.get("_key_prefix", "")),
        "model": str(ctx.body.get("model", "") or ""),
        "provider": str(ctx.metadata.get("_provider", "") or ""),
        "status": status,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "cost_usd": 0.0,
        "latency_ms": round((time.time() - started) * 1000, 1),
        "blocked": refused,
        "block_reason": reason,
        "metadata": json.dumps(
            {"event": "request.refused" if refused else "request.failed"},
            separators=(",", ":"),
        ),
    }

    async def _write() -> None:
        try:
            await store.log_audit(**row)
            MetricsTracker.track_audit_persistence("pipeline", "ok")
        except Exception as e:
            MetricsTracker.track_audit_persistence("pipeline", "fail")
            logger.warning("Refusal audit log failed: %s", e)

    await submit_audit(orchestrator, _write(), route="pipeline")


def _new_context(
    orchestrator: Any, request: Any, body: dict[str, Any], session_id: str, req_id: str
) -> PluginContext:
    ctx = PluginContext(
        request=request,
        body=body,
        session_id=session_id,
        metadata={
            "rotator": orchestrator,
            "req_id": req_id,
            "_cache_control": request.headers.get("cache-control", "")
            if request
            else "",
        },
        state=orchestrator.plugin_state,
    )
    # Who the request is accounted to. The forwarder writes the audit and spend
    # rows of a stream from this; nothing used to set it, so every streamed
    # request was recorded with no caller.
    ctx.metadata["_key_prefix"] = audit_principal(request)
    # Which data-plane route this is, for the token/cost series a stream feeds
    # after the fact (the matched template, never an arbitrary caller string).
    route = getattr(getattr(request, "url", None), "path", None)
    if route in ("/v1/chat/completions", "/v1/completions"):
        ctx.metadata["_route"] = route
    return ctx


async def _run_ring(orchestrator: Any, hook: PluginHook, ctx: PluginContext, label: str) -> None:
    started = time.perf_counter()
    await orchestrator.plugin_manager.execute_ring(hook, ctx)
    MetricsTracker.track_ring_latency(label, time.perf_counter() - started)


async def _screen(orchestrator: Any, ctx: PluginContext, request: Any, session_id: str) -> None:
    """Negative cache, then the SecurityShield (injection, trajectory, cross-session)."""
    # L1: Negative Cache — drop repeated attacks in <0.1ms
    neg_reason = orchestrator.negative_cache.check(ctx.body)
    if neg_reason:
        logger.debug(f"L1 Negative Cache drop: {neg_reason[:50]}")
        MetricsTracker.track_injection_blocked()
        raise HTTPException(status_code=403, detail=neg_reason)

    client_ip = request.client.host if hasattr(request, "client") and request.client else ""
    key_prefix = session_id[:8] if session_id != "default" else ""
    security_error = await orchestrator.security.inspect(
        ctx.body,
        session_id,
        ip=client_ip,
        key_prefix=key_prefix,
    )
    if security_error:
        logger.warning(f"SecurityShield blocked: {security_error}")
        MetricsTracker.track_injection_blocked()
        orchestrator.negative_cache.add(ctx.body, security_error)
        raise HTTPException(status_code=403, detail=security_error)


async def _ingress(orchestrator: Any, ctx: PluginContext, session_id: str) -> None:
    """RING 1: auth, zero-trust, rate limit."""
    await _run_ring(orchestrator, PluginHook.INGRESS, ctx, "ingress")
    if ctx.stop_chain:
        MetricsTracker.track_injection_blocked()
        orchestrator._spawn_task(
            orchestrator.webhooks.dispatch(
                EventType.INJECTION_BLOCKED,
                {
                    "reason": ctx.error or "Ingress Blocked",
                    "session": session_id[:8],
                },
            )
        )
        raise HTTPException(status_code=403, detail=ctx.error or "Ingress Blocked")


async def _pre_flight(orchestrator: Any, ctx: PluginContext) -> Any:
    """RING 2: PII masking, budget guard, loop breaker, cache lookup.

    Returns _CONTINUE, or the response to hand back at once (a cache hit).
    """
    await _run_ring(orchestrator, PluginHook.PRE_FLIGHT, ctx, "pre_flight")
    if not ctx.stop_chain:
        return _CONTINUE
    # Two legitimate stop-chain paths in PRE_FLIGHT:
    #   1. cache_hit  — semantic_cache plugin set ctx.response
    #   2. action=block — budget_guard / loop_breaker / similar set
    #      ctx.error + _block_status but NO ctx.response
    # Without this distinction the block path falls through with
    # ctx.response=None and FastAPI returns a 500 Internal Error
    # instead of the proper 4xx the plugin asked for.
    if ctx.metadata.get("_cache_hit"):
        if ctx.body.get("stream"):
            cached_data = ctx.metadata.get("_cached_response_data")
            if cached_data:
                ctx.response = StreamingResponse(
                    fake_stream(cached_data),
                    media_type="text/event-stream",
                    headers={"X-LLMProxy-Cache": "HIT"},
                )
        return ctx.response
    # Block path — surface the plugin's status + error.
    raise HTTPException(
        status_code=ctx.metadata.get("_block_status", 403),
        detail=ctx.error or "Request blocked by pre-flight plugin",
    )


def _resolve_model(orchestrator: Any, ctx: PluginContext) -> None:
    """Model alias/group resolution, before routing."""
    original_model = ctx.body.get("model", "")
    resolved_model, resolved_provider = resolve_model(orchestrator.config, original_model)
    if resolved_model != original_model:
        ctx.body["model"] = resolved_model
        ctx.metadata["_model_alias"] = original_model
    # When resolved from a group, pin the provider so the smart
    # router picks the matching endpoint (not a random one).
    if resolved_provider:
        ctx.metadata["_resolved_provider"] = resolved_provider


async def _flag_budget(
    orchestrator: Any, ctx: PluginContext, request: Any, body: dict[str, Any]
) -> None:
    """FinOps routing: flag a request that would cross the daily limit or the app quota.

    The verdict is a flag the forwarder acts on (HTTP 402), not a stop here. It is
    predictive: it uses the request's estimated cost, not only the running total.
    """
    from core.pricing import estimate_cost_pre_flight
    from core.tokenizer import count_messages_tokens

    budget_cfg = orchestrator.config.get("budget", {})
    daily_limit = budget_cfg.get("daily_limit", 50.0)

    # Predictive Token Estimation
    input_tokens = count_messages_tokens(body.get("messages", []), body.get("model", ""))
    predictive_cost = estimate_cost_pre_flight(body.get("model", ""), input_tokens)

    async with orchestrator._budget_lock:
        roll_over_if_new_day(orchestrator)
        # Block if the expected cost of this request would push us over the limit
        global_over_budget = (orchestrator.total_cost_today + predictive_cost) >= daily_limit

    app_over_budget = getattr(request.state, "quota_exceeded", False)

    if global_over_budget or app_over_budget:
        ctx.metadata["_budget_saturated"] = True
        if global_over_budget:
            await orchestrator._add_log(
                f"BUDGET SATURATED (Global): (${orchestrator.total_cost_today:.2f} + {predictive_cost:.4f} est >= ${daily_limit:.2f})",
                level="PROXY",
            )
        else:
            await orchestrator._add_log(
                "BUDGET SATURATED (App Quota)",
                level="PROXY",
            )


async def _route(orchestrator: Any, ctx: PluginContext) -> None:
    """RING 3: pick the target endpoint."""
    await _run_ring(orchestrator, PluginHook.ROUTING, ctx, "routing")
    if ctx.stop_chain:
        raise HTTPException(status_code=503, detail=ctx.error or "No Routing Target")


async def _forward(orchestrator: Any, ctx: PluginContext) -> None:
    """Forward with cross-provider fallback, charge the budget, record endpoint stats."""
    target = ctx.metadata.get("target_endpoint")
    # The upstream request carries the operator's provider key. Its headers
    # used to start from a ``headers`` object in the request body, so an
    # inference client chose them (Host, Content-Length, a provider's beta or
    # organisation header). The key is dropped: it is not part of any API this
    # proxy speaks, and forwarded as a field it makes a strict upstream refuse
    # the request.
    ctx.body.pop("headers", None)
    headers = dict(orchestrator.zt_manager.get_identity_headers())

    start_req = time.time()
    session = await orchestrator._get_session()
    # Per-request delta dict: forwarder accumulates only the cost
    # increment for this request; rotator adds it atomically under
    # budget_lock, preventing lost-update when concurrent streams
    # each started from the same total_cost_today snapshot.
    cost_ref: dict[str, Any] = {"delta": 0.0}
    # Pass budget_lock to cost_ref so the stream generator can charge
    # the budget atomically when it finishes (streaming responses
    # return immediately — cost_ref["delta"] is still 0.0 here).
    cost_ref["_budget_lock"] = orchestrator._budget_lock
    cost_ref["_rotator"] = orchestrator  # ref for total_cost_today update
    await orchestrator.forwarder.forward_with_fallback(
        ctx,
        target,
        headers,
        session,
        cost_ref=cost_ref,
    )
    # For non-streaming responses, charge budget immediately.
    # For streaming, the charge happens in the stream generator's
    # finally block (see forwarder._handle_streaming).
    #
    # Through charge_and_persist, not by incrementing the counter here.
    # This site used to do the increment inline under the same lock but
    # without enqueuing the persistence write, so the in-memory total
    # advanced and the app_state row did not. /v1/chat/completions was
    # covered by accident — its route enqueues the total separately — but
    # /v1/completions reaches this path with no route-level enqueue, so a
    # non-streaming workload there advanced the running total and left the
    # persisted value where the last chat request had put it. On restart
    # hydrate_daily_total read that stale row and the day's spend reset
    # downward, while the daily limit kept being enforced against it.
    #
    # charge_and_persist acquires the lock itself, so it must not be held
    # here. It is also what forwarder._handle_streaming and the embeddings
    # route call, which makes this the third and last charging site to go
    # through one helper.
    if not isinstance(ctx.response, StreamingResponse):
        # The forwarder fills in ``delta`` for a stream only (when it ends). For
        # a plain response it was left at 0.0 and charge_and_persist returns on
        # a zero amount, so no non-streaming request was ever counted against
        # the daily limit: the cap, the budget gauge and both budget alerts saw
        # streamed traffic alone.
        if not cost_ref["delta"]:
            cost_ref["delta"] = _served_cost(ctx)
        await charge_and_persist(orchestrator, orchestrator._budget_lock, cost_ref["delta"])

    ctx.metadata["duration"] = time.time() - start_req

    # Update endpoint performance stats for smart routing
    routed_endpoint_id = getattr(
        ctx.metadata.get("target_endpoint"),
        "id",
        ctx.metadata.get("_provider", "unknown"),
    )
    success = (
        ctx.response and hasattr(ctx.response, "status_code") and ctx.response.status_code < 400
    )
    await update_endpoint_stats(
        routed_endpoint_id,
        ctx.metadata["duration"] * 1000,
        bool(success),
        redis_client=getattr(orchestrator, "redis_client", None),
    )


async def _tool_policy(orchestrator: Any, ctx: PluginContext) -> None:
    """Refuse a response whose tool calls the policy does not allow.

    Non-streaming responses only: a stream is judged by the forwarder as the
    call appears (see RequestForwarder._handle_streaming). The refusal is a
    403 raised from here, so it is recorded like every other refusal, in the
    audit chain, with the tool's name.
    """
    policy = ToolPolicy.from_config(orchestrator.config)
    raw = getattr(ctx.response, "body", None)
    if not policy.enabled or not raw or isinstance(ctx.response, StreamingResponse):
        return
    try:
        choices = json.loads(raw).get("choices") or []
    except (ValueError, AttributeError, UnicodeDecodeError):
        return
    after_data = follows_tool_result(ctx.body.get("messages"))
    for choice in choices:
        message = choice.get("message") if isinstance(choice, dict) else None
        for name in called_tools(message):
            reason = policy.refusal(name, after_data)
            if reason is None:
                continue
            if not policy.enforce:
                MetricsTracker.track_tool_policy("would_refuse")
                await orchestrator._add_log(
                    f"TOOL POLICY (log only): call to '{name}' would be refused: {reason}",
                    level="SECURITY",
                )
                continue
            MetricsTracker.track_tool_policy("refused")
            await orchestrator._add_log(
                f"TOOL POLICY: call to '{name}' refused: {reason}", level="SECURITY"
            )
            raise HTTPException(
                status_code=403, detail=f"Tool call '{name}' refused by policy: {reason}"
            )


async def _post_flight(orchestrator: Any, ctx: PluginContext) -> None:
    """RING 4: response sanitization, watermarking."""
    await _run_ring(orchestrator, PluginHook.POST_FLIGHT, ctx, "post_flight")
    if ctx.stop_chain:
        # Same path as a pre-flight block: the OpenAI envelope and the status
        # the plugin asked for. This returned a bare {"error": "<text>"} outside
        # the documented shape, whatever status the plugin set.
        raise HTTPException(
            status_code=ctx.metadata.get("_block_status", 403),
            detail=ctx.error or "Response blocked by post-flight plugin",
        )


async def _background_ring(orchestrator: Any, ctx: PluginContext) -> None:
    """RING 5: telemetry, export, cache write. Runs after the response is on its way."""
    started = time.perf_counter()
    await orchestrator.plugin_manager.execute_ring(PluginHook.BACKGROUND, ctx)
    MetricsTracker.track_ring_latency("background", time.perf_counter() - started)

    cache_key = ctx.metadata.get("_cache_key")
    if (
        cache_key
        and orchestrator.cache_backend._enabled
        and not ctx.metadata.get("_cache_bypass")
        and ctx.response
        and hasattr(ctx.response, "body")
        and not ctx.metadata.get("_cache_hit")
    ):
        try:
            response_data = json.loads(ctx.response.body.decode())
            content = (
                response_data.get("choices", [{}])[0].get("message", {}).get("content", "")
            )
            if "[SEC_ERR:" not in content:
                await orchestrator.cache_backend.put(
                    body=ctx.body,
                    response_data=response_data,
                    tenant_id=ctx.metadata.get("_cache_tenant", ctx.session_id),
                    model=ctx.body.get("model", ""),
                )
        except Exception as e:
            logger.debug(f"Cache write skipped: {e}")


def _decorate_response(orchestrator: Any, ctx: PluginContext) -> None:
    """Proxy metadata headers, the budget-downgrade headers and the response signature."""
    if not (ctx.response and hasattr(ctx.response, "headers")):
        return
    cache_status = ctx.metadata.get("_cache_status", "")
    if cache_status:
        ctx.response.headers["X-LLMProxy-Cache"] = cache_status
    ctx.response.headers["X-LLMProxy-Provider"] = ctx.metadata.get("_provider", "")
    ctx.response.headers["X-LLMProxy-Request-Id"] = ctx.metadata.get("req_id", "")
    # Budget downgrade notification headers
    for k, v in ctx.metadata.get("_budget_downgrade_headers", {}).items():
        ctx.response.headers[k] = v

    # S2: Cryptographic response signing
    if orchestrator.response_signer.enabled and hasattr(ctx.response, "body"):
        sig_headers = orchestrator.response_signer.sign_response(
            response_body=ctx.response.body,
            model=ctx.body.get("model", ""),
            provider=ctx.metadata.get("_provider", ""),
            request_id=ctx.metadata.get("req_id", ""),
        )
        for k, v in sig_headers.items():
            ctx.response.headers[k] = v


def _annotate_trace(orchestrator: Any, ctx: PluginContext, start_total: float) -> None:
    """Store the total pipeline latency in the request trace (O(1) via index dict)."""
    total_ms = (time.time() - start_total) * 1000
    req_id = ctx.metadata.get("req_id", "unknown")
    fields = {
        "total_ms": round(total_ms, 2),
        "upstream_ms": round(ctx.metadata.get("duration", 0) * 1000, 2),
    }
    if "ttft_ms" in ctx.metadata:
        fields["ttft_ms"] = ctx.metadata["ttft_ms"]
    orchestrator.plugin_manager.annotate_ring_trace(req_id, **fields)

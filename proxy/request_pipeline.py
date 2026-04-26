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
from typing import Any, Dict

from fastapi import HTTPException
from fastapi.responses import JSONResponse, StreamingResponse

from core.metrics import MetricsTracker
from core.model_resolver import resolve_model
from core.plugin_engine import PluginContext, PluginHook
from core.stream_faker import fake_stream
from core.tracing import TraceManager
from core.webhooks import EventType
from plugins.default.neural_router import update_endpoint_stats

logger = logging.getLogger("llmproxy.request_pipeline")


async def process_proxy_request(
    orchestrator: Any,
    request: Any,
    body: Dict[str, Any] | None = None,
    session_id: str = "default",
):
    """Run a request through the 5-ring pipeline.

    `orchestrator` is the live ProxyOrchestrator — every subsystem is
    read off it (security, plugin_manager, forwarder, cache_backend,
    response_signer, webhooks, zt_manager, …). This keeps coupling tight
    while letting the dispatch logic live in its own file.
    """
    start_total = time.time()
    if body is None:
        body = await request.json()

    ctx = PluginContext(
        request=request,
        body=body,
        session_id=session_id,
        metadata={
            "rotator": orchestrator,
            "req_id": uuid.uuid4().hex[:16],
            "_cache_control": request.headers.get("cache-control", "") if request else "",
        },
        state=orchestrator.plugin_state,
    )

    try:
        # L1: Negative Cache — drop repeated attacks in <0.1ms
        neg_reason = orchestrator.negative_cache.check(ctx.body)
        if neg_reason:
            logger.debug(f"L1 Negative Cache drop: {neg_reason[:50]}")
            MetricsTracker.track_injection_blocked()
            raise HTTPException(status_code=403, detail=neg_reason)

        # Pre-ring: SecurityShield (injection scoring + trajectory + cross-session)
        client_ip = request.client.host if hasattr(request, 'client') and request.client else ""
        key_prefix = session_id[:8] if session_id != "default" else ""
        security_error = await orchestrator.security.inspect(
            ctx.body, session_id, ip=client_ip, key_prefix=key_prefix,
        )
        if security_error:
            logger.warning(f"SecurityShield blocked: {security_error}")
            MetricsTracker.track_injection_blocked()
            orchestrator.negative_cache.add(ctx.body, security_error)
            raise HTTPException(status_code=403, detail=security_error)

        # RING 1: INGRESS (Auth, ZT, Rate Limit)
        r1_start = time.perf_counter()
        await orchestrator.plugin_manager.execute_ring(PluginHook.INGRESS, ctx)
        MetricsTracker.track_ring_latency("ingress", time.perf_counter() - r1_start)
        if ctx.stop_chain:
            MetricsTracker.track_injection_blocked()
            orchestrator._spawn_task(orchestrator.webhooks.dispatch(
                EventType.INJECTION_BLOCKED,
                {"reason": ctx.error or "Ingress Blocked", "session": session_id[:8]},
            ))
            raise HTTPException(status_code=403, detail=ctx.error or "Ingress Blocked")

        # RING 2: PRE-FLIGHT (PII masking, budget guard, loop breaker, cache lookup)
        r2_start = time.perf_counter()
        await orchestrator.plugin_manager.execute_ring(PluginHook.PRE_FLIGHT, ctx)
        MetricsTracker.track_ring_latency("pre_flight", time.perf_counter() - r2_start)
        if ctx.stop_chain:
            if ctx.metadata.get("_cache_hit") and ctx.body.get("stream"):
                cached_data = ctx.metadata.get("_cached_response_data")
                if cached_data:
                    ctx.response = StreamingResponse(
                        fake_stream(cached_data), media_type="text/event-stream",
                        headers={"X-LLMProxy-Cache": "HIT"},
                    )
            return ctx.response

        # Model alias/group resolution (before routing)
        original_model = ctx.body.get("model", "")
        resolved_model, resolved_provider = resolve_model(orchestrator.config, original_model)
        if resolved_model != original_model:
            ctx.body["model"] = resolved_model
            ctx.metadata["_model_alias"] = original_model
        # When resolved from a group, pin the provider so the smart
        # router picks the matching endpoint (not a random one).
        if resolved_provider:
            ctx.metadata["_resolved_provider"] = resolved_provider

        # Budget-aware model downgrade: if over hard limit, fall back to local
        budget_cfg = orchestrator.config.get("budget", {})
        if budget_cfg.get("fallback_to_local_on_limit"):
            daily_limit = budget_cfg.get("daily_limit", 50.0)
            async with orchestrator._budget_lock:
                over_budget = orchestrator.total_cost_today >= daily_limit
            if over_budget:
                local_model = budget_cfg.get("local_model", "ollama/llama3.3")
                original_before_downgrade = ctx.body.get("model", "")
                ctx.metadata["_budget_downgrade"] = True
                ctx.metadata["_original_model_pre_downgrade"] = original_before_downgrade
                ctx.body["model"] = local_model
                ctx.metadata["_budget_downgrade_headers"] = {
                    "X-LLMProxy-Model-Downgraded": "true",
                    "X-LLMProxy-Original-Model": original_before_downgrade,
                    "X-LLMProxy-Downgrade-Reason": "daily_budget_exceeded",
                }
                await orchestrator._add_log(
                    f"BUDGET DOWNGRADE: {original_before_downgrade} → {local_model} "
                    f"(${orchestrator.total_cost_today:.2f}/${daily_limit:.2f})",
                    level="PROXY",
                )

        # RING 3: ROUTING
        r3_start = time.perf_counter()
        await orchestrator.plugin_manager.execute_ring(PluginHook.ROUTING, ctx)
        MetricsTracker.track_ring_latency("routing", time.perf_counter() - r3_start)
        if ctx.stop_chain:
            raise HTTPException(status_code=503, detail=ctx.error or "No Routing Target")

        target = ctx.metadata.get("target_endpoint")
        headers = ctx.body.get("headers", {})
        headers.update(orchestrator.zt_manager.get_identity_headers())

        # Forward request with cross-provider fallback
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
            ctx, target, headers, session, cost_ref=cost_ref,
        )
        # For non-streaming responses, charge budget immediately.
        # For streaming, the charge happens in the stream generator's
        # finally block (see forwarder._handle_streaming).
        if not isinstance(ctx.response, StreamingResponse):
            async with orchestrator._budget_lock:
                orchestrator.total_cost_today += cost_ref["delta"]

        ctx.metadata["duration"] = time.time() - start_req

        # Update endpoint performance stats for smart routing
        routed_endpoint_id = getattr(
            ctx.metadata.get("target_endpoint"), 'id',
            ctx.metadata.get("_provider", "unknown"),
        )
        success = ctx.response and hasattr(ctx.response, "status_code") and ctx.response.status_code < 400
        await update_endpoint_stats(routed_endpoint_id, ctx.metadata["duration"] * 1000, bool(success))

        # RING 4: POST-FLIGHT (response sanitization, watermarking)
        r4_start = time.perf_counter()
        await orchestrator.plugin_manager.execute_ring(PluginHook.POST_FLIGHT, ctx)
        MetricsTracker.track_ring_latency("post_flight", time.perf_counter() - r4_start)
        if ctx.stop_chain:
            return JSONResponse(content={"error": ctx.error}, status_code=403)

        # RING 5: BACKGROUND (telemetry, export, cache write)
        async def _bg_ring():
            r5_start = time.perf_counter()
            await orchestrator.plugin_manager.execute_ring(PluginHook.BACKGROUND, ctx)
            MetricsTracker.track_ring_latency("background", time.perf_counter() - r5_start)

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
                    content = response_data.get("choices", [{}])[0].get("message", {}).get("content", "")
                    if "[SEC_ERR:" not in content:
                        await orchestrator.cache_backend.put(
                            body=ctx.body,
                            response_data=response_data,
                            tenant_id=ctx.metadata.get("_cache_tenant", ctx.session_id),
                            model=ctx.body.get("model", ""),
                        )
                except Exception as e:
                    logger.debug(f"Cache write skipped: {e}")

        orchestrator._spawn_task(_bg_ring())

        # Inject proxy metadata headers on responses
        if ctx.response and hasattr(ctx.response, "headers"):
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

        # Store total pipeline latency in trace (O(1) via index dict)
        total_ms = (time.time() - start_total) * 1000
        req_id = ctx.metadata.get("req_id", "unknown")
        trace = orchestrator.plugin_manager._ring_traces_index.get(req_id)
        if trace:
            trace["total_ms"] = round(total_ms, 2)
            trace["upstream_ms"] = round(ctx.metadata.get("duration", 0) * 1000, 2)
            if "ttft_ms" in ctx.metadata:
                trace["ttft_ms"] = ctx.metadata["ttft_ms"]

        return ctx.response

    except HTTPException:
        raise
    except Exception as e:
        orchestrator.logger.error(f"Proxy pipeline error: {e}")
        TraceManager.capture_exception(e)
        raise HTTPException(status_code=502, detail="Upstream request failed")

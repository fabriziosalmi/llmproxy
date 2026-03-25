"""
LLMPROXY — Request Forwarder.

Handles upstream forwarding with cross-provider fallback, circuit breaker
integration, streaming support, and post-stream budget charging.
"""

import json
import time
import asyncio
import logging
from typing import Any, Callable, Awaitable

import aiohttp

from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from core.metrics import MetricsTracker

logger = logging.getLogger("llmproxy.forwarder")


class RequestForwarder:
    """Forwards requests to upstream LLM providers with fallback chain support."""

    def __init__(
        self,
        config: dict,
        circuit_manager: Any,
        budget_lock: asyncio.Lock,
        get_session: Callable[[], Awaitable[Any]],
        add_log: Callable[..., Awaitable[None]],
    ):
        self.config = config
        self.circuit_manager = circuit_manager
        self._budget_lock = budget_lock
        self._get_session = get_session
        self._add_log = add_log
        # Mutable reference to agent's total_cost_today — updated via charge_budget()
        self._cost_ref: dict[str, float] = {}

    def bind_cost_ref(self, ref: dict[str, float]):
        """Bind a mutable dict {"total": float} so streaming can charge budget."""
        self._cost_ref = ref

    def resolve_endpoint_for_provider(self, provider: str) -> Any:
        """Resolve the configured endpoint URL for a provider."""
        endpoints_cfg = self.config.get("endpoints", {})
        for ep_name, ep_config in endpoints_cfg.items():
            if ep_config.get("provider") == provider or ep_name == provider:
                base_url = ep_config.get("base_url", "")
                from types import SimpleNamespace
                return SimpleNamespace(
                    id=ep_name,
                    url=base_url,
                    provider=provider,
                    provider_type=provider,
                )
        return None

    async def forward_request(self, ctx, adapter, target_url, translated_body,
                              translated_headers, session, cb, endpoint_id):
        """Forward a single request (non-streaming) with circuit breaker tracking."""
        response = await adapter.request(target_url, translated_body, translated_headers, session)
        if response.status_code in (429, 500, 502, 503, 504):
            cb.report_failure()
            raise HTTPException(
                status_code=response.status_code,
                detail=f"Upstream {endpoint_id} returned {response.status_code}",
            )
        cb.report_success()
        return response

    async def forward_with_fallback(self, ctx, target, headers, session):
        """Forward request with cross-provider fallback on failure.

        Tries the primary endpoint first. On failure (circuit open, HTTP error,
        connection error), walks the fallback_chain for the requested model.
        """
        from .adapters.registry import get_adapter

        original_model = ctx.body.get("model", "")
        original_body = dict(ctx.body)
        attempts = []

        # Build attempt list: primary + fallback chain
        primary_provider = getattr(target, 'provider', None) or getattr(target, 'provider_type', None)
        primary_adapter = get_adapter(primary_provider, original_model)
        attempts.append({
            "target": target,
            "adapter": primary_adapter,
            "model": original_model,
            "provider": primary_adapter.provider_name,
            "is_fallback": False,
        })

        # Add fallback chain entries
        chain = self.config.get("fallback_chains", {}).get(original_model, [])
        for fb in chain:
            fb_target = self.resolve_endpoint_for_provider(fb["provider"])
            if fb_target:
                fb_adapter = get_adapter(fb["provider"])
                attempts.append({
                    "target": fb_target,
                    "adapter": fb_adapter,
                    "model": fb["model"],
                    "provider": fb["provider"],
                    "is_fallback": True,
                })

        last_error: Exception | None = None
        for i, attempt in enumerate(attempts):
            a_target = attempt["target"]
            a_adapter = attempt["adapter"]
            a_model = attempt["model"]
            endpoint_id = getattr(a_target, 'id', str(a_target.url)) if a_target else 'unknown'

            # Circuit breaker check
            cb = self.circuit_manager.get_breaker(endpoint_id)
            if not cb.can_execute():
                if attempt["is_fallback"]:
                    continue
                last_error = HTTPException(
                    status_code=503,
                    detail=f"Circuit open for endpoint '{endpoint_id}'",
                )
                continue

            # Set model for this attempt
            ctx.body["model"] = a_model
            ctx.metadata["_provider"] = a_adapter.provider_name
            if attempt["is_fallback"]:
                ctx.metadata["_fallback_used"] = attempt["provider"]
                ctx.metadata["_fallback_model"] = a_model
                ctx.metadata["_original_model"] = original_model
                await self._add_log(
                    f"FALLBACK: {original_model} → {a_model} ({attempt['provider']})",
                    level="PROXY",
                )

            # Translate request for this provider
            target_url, translated_body, translated_headers = a_adapter.translate_request(
                str(a_target.url), ctx.body, headers,
            )

            try:
                if ctx.body.get("stream") or original_body.get("stream"):
                    return await self._handle_streaming(
                        ctx, a_adapter, target_url, translated_body,
                        translated_headers, session, cb, endpoint_id,
                    )
                else:
                    ctx.response = await self.forward_request(
                        ctx, a_adapter, target_url, translated_body,
                        translated_headers, session, cb, endpoint_id,
                    )
                    return ctx.response

            except (HTTPException, asyncio.TimeoutError, aiohttp.ClientError, OSError, ValueError, RuntimeError) as e:
                last_error = e
                if not attempt["is_fallback"]:
                    await self._add_log(
                        f"PRIMARY FAILED: {endpoint_id} — {e}", level="PROXY",
                    )
                continue

        # All attempts exhausted
        ctx.body["model"] = original_model
        if last_error:
            raise last_error
        raise HTTPException(status_code=503, detail="All providers failed")

    async def _handle_streaming(self, ctx, adapter, target_url, translated_body,
                                translated_headers, session, cb, endpoint_id):
        """Handle streaming response with TTFT tracking and post-stream budget charging."""
        ttft_start = time.perf_counter()
        first_chunk_seen = False
        circuit_success_reported = False
        budget_lock = self._budget_lock
        cost_ref = self._cost_ref

        async def stream_generator():
            nonlocal first_chunk_seen, circuit_success_reported
            stream_usage = {}
            try:
                async for chunk in adapter.stream(target_url, translated_body, translated_headers, session):
                    if not first_chunk_seen:
                        first_chunk_seen = True
                        cb.report_success()
                        circuit_success_reported = True
                        ttft = time.perf_counter() - ttft_start
                        MetricsTracker.track_ttft(endpoint_id, ttft)
                        ctx.metadata["ttft_ms"] = round(ttft * 1000, 2)
                    # Extract usage from final SSE chunks (OpenAI/Anthropic/Google)
                    if b'"usage"' in chunk or b'"usageMetadata"' in chunk:
                        try:
                            for line in chunk.decode("utf-8", errors="replace").split("\n"):
                                if line.startswith("data: ") and line[6:].strip() != "[DONE]":
                                    d = json.loads(line[6:])
                                    u = d.get("usage") or d.get("usageMetadata", {})
                                    if u:
                                        stream_usage = u
                        except (json.JSONDecodeError, KeyError, UnicodeDecodeError):
                            pass
                    yield chunk
            except (asyncio.TimeoutError, OSError, RuntimeError) as e:
                if not circuit_success_reported:
                    cb.report_failure()
                raise e
            finally:
                # Post-stream: update budget with real token cost
                # In finally block to charge even on client disconnect
                if stream_usage:
                    from core.pricing import estimate_cost
                    p_tok = stream_usage.get("prompt_tokens") or stream_usage.get("promptTokenCount", 0)
                    c_tok = stream_usage.get("completion_tokens") or stream_usage.get("candidatesTokenCount", 0)
                    model_name = ctx.body.get("model", "")
                    real_cost = estimate_cost(model_name, p_tok, c_tok)
                    async with budget_lock:
                        cost_ref["total"] = cost_ref.get("total", 0.0) + real_cost
                    ctx.metadata["_stream_usage"] = {"prompt_tokens": p_tok, "completion_tokens": c_tok}
                    ctx.metadata["_stream_cost_usd"] = round(real_cost, 6)

        ctx.response = StreamingResponse(stream_generator(), media_type="text/event-stream")
        return ctx.response

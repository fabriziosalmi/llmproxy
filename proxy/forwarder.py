"""
LLMPROXY — Request Forwarder.

Handles upstream forwarding with cross-provider fallback, circuit breaker
integration, streaming support, and post-stream budget charging.
"""

import json
import time
import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable

import aiohttp

from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from core.metrics import MetricsTracker

logger = logging.getLogger("llmproxy.forwarder")


@dataclass
class ForwardingContext:
    """Decouples the forwarder from the orchestrator's internals.

    Instead of passing raw locks, callbacks, and object references,
    the orchestrator builds this context once and hands it over.
    """
    config: dict = field(default_factory=dict)
    circuit_manager: Any = None
    budget_lock: asyncio.Lock | None = None
    get_session: Callable[[], Awaitable[Any]] | None = None
    add_log: Callable[..., Awaitable[None]] | None = None
    security: Any = None  # SecurityShield for mid-stream PII/injection monitoring


class RequestForwarder:
    """Forwards requests to upstream LLM providers with fallback chain support."""

    def __init__(
        self,
        config: dict | None = None,
        circuit_manager: Any = None,
        budget_lock: asyncio.Lock | None = None,
        get_session: Callable[[], Awaitable[Any]] | None = None,
        add_log: Callable[..., Awaitable[None]] | None = None,
        security: Any = None,
        *,
        ctx: ForwardingContext | None = None,
    ):
        if ctx is not None:
            self.config = ctx.config
            self.circuit_manager = ctx.circuit_manager
            self._budget_lock = ctx.budget_lock
            self._get_session = ctx.get_session
            self._add_log = ctx.add_log
            self._security = ctx.security
        else:
            self.config = config or {}
            self.circuit_manager = circuit_manager
            self._budget_lock = budget_lock
            self._get_session = get_session
            self._add_log = add_log
            self._security = security

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
            await cb.report_failure()
            raise HTTPException(
                status_code=response.status_code,
                detail=f"Upstream {endpoint_id} returned {response.status_code}",
            )
        await cb.report_success()
        return response

    async def forward_with_fallback(self, ctx, target, headers, session,
                                    cost_ref: "dict[str, Any] | None" = None):
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
            cb = await self.circuit_manager.get_breaker(endpoint_id)
            if not await cb.can_execute():
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
                if self._add_log:
                    await self._add_log(
                        f"FALLBACK: {original_model} → {a_model} ({attempt['provider']})",
                        level="PROXY",
                    )

            # Inject provider API key from endpoint config
            provider_headers = dict(headers)
            ep_metadata = getattr(a_target, 'metadata', {}) or {}
            api_key_env = ep_metadata.get("api_key_env", "")
            if api_key_env:
                import os
                api_key = os.environ.get(api_key_env, "")
                if api_key:
                    provider_headers["Authorization"] = f"Bearer {api_key}"

            # Translate request for this provider
            target_url, translated_body, translated_headers = a_adapter.translate_request(
                str(a_target.url), ctx.body, provider_headers,
            )

            try:
                if ctx.body.get("stream") or original_body.get("stream"):
                    return await self._handle_streaming(
                        ctx, a_adapter, target_url, translated_body,
                        translated_headers, session, cb, endpoint_id,
                        cost_ref=cost_ref,
                    )
                else:
                    ctx.response = await self.forward_request(
                        ctx, a_adapter, target_url, translated_body,
                        translated_headers, session, cb, endpoint_id,
                    )
                    return ctx.response

            except (asyncio.TimeoutError, aiohttp.ClientError, OSError) as e:
                # Retryable: network/timeout errors → try next provider
                last_error = e
                if not attempt["is_fallback"] and self._add_log:
                    await self._add_log(
                        f"PRIMARY FAILED (retryable): {endpoint_id} — {type(e).__name__}: {e}",
                        level="PROXY",
                    )
                continue
            except HTTPException as e:
                # Permanent client errors (4xx except 429) → don't fallback
                if 400 <= e.status_code < 500 and e.status_code != 429:
                    raise
                # 429/5xx are retryable
                last_error = e
                if not attempt["is_fallback"] and self._add_log:
                    await self._add_log(
                        f"PRIMARY FAILED (retryable): {endpoint_id} — HTTP {e.status_code}",
                        level="PROXY",
                    )
                continue

        # All attempts exhausted
        ctx.body["model"] = original_model
        if last_error:
            raise last_error
        raise HTTPException(status_code=503, detail="All providers failed")

    async def _handle_streaming(self, ctx, adapter, target_url, translated_body,
                                translated_headers, session, cb, endpoint_id,
                                cost_ref: "dict[str, Any] | None" = None):
        """Handle streaming response with TTFT tracking and post-stream budget charging."""
        ttft_start = time.perf_counter()
        first_chunk_seen = False
        circuit_success_reported = False
        # cost_ref is passed per-request — never shared across concurrent requests
        if cost_ref is None:
            cost_ref = {}

        # Mid-stream speculative guardrail: launch analyze_speculative() as a
        # background task that monitors the accumulating response text for PII
        # leakage or injection patterns.  Previously this method existed but
        # was never wired into the streaming path (dead code).
        stream_text_chunks: list[str] = []
        kill_event = asyncio.Event()
        speculative_task: asyncio.Task | None = None
        if self._security:
            prompt = ""
            messages = ctx.body.get("messages", [])
            if messages:
                prompt = str(messages[-1].get("content", ""))
            speculative_task = asyncio.create_task(
                self._security.analyze_speculative(prompt, stream_text_chunks, kill_event)
            )

        async def stream_generator():
            nonlocal first_chunk_seen, circuit_success_reported
            stream_usage = {}
            try:
                async for chunk in adapter.stream(target_url, translated_body, translated_headers, session):
                    # Abort stream if speculative guardrail fired
                    if kill_event.is_set():
                        logger.warning(
                            f"STREAM ABORTED by speculative guardrail (endpoint={endpoint_id})"
                        )
                        yield (
                            b'data: {"error":"stream_blocked",'
                            b'"message":"Response blocked by content policy"}\n\n'
                        )
                        return

                    if not first_chunk_seen:
                        first_chunk_seen = True
                        await cb.report_success()
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
                    # Feed decoded text to the speculative analyzer
                    if speculative_task is not None:
                        try:
                            stream_text_chunks.append(chunk.decode("utf-8", errors="replace"))
                        except Exception:
                            pass
                    yield chunk
            except (asyncio.TimeoutError, OSError, RuntimeError) as e:
                if not circuit_success_reported:
                    await cb.report_failure()
                raise e
            finally:
                # Signal speculative task to stop and cancel if still running
                kill_event.set()
                if speculative_task is not None and not speculative_task.done():
                    speculative_task.cancel()
                # Post-stream: update budget with real token cost
                # In finally block to charge even on client disconnect
                from core.pricing import estimate_cost
                from core.tokenizer import count_tokens
                model_name = ctx.body.get("model", "")
                if stream_usage:
                    p_tok = stream_usage.get("prompt_tokens") or stream_usage.get("promptTokenCount", 0)
                    c_tok = stream_usage.get("completion_tokens") or stream_usage.get("candidatesTokenCount", 0)
                else:
                    # Fallback: estimate tokens from accumulated text when
                    # provider omits usage chunk (prevents budget bypass).
                    prompt_text = " ".join(
                        str(m.get("content", "")) for m in ctx.body.get("messages", [])
                    )
                    response_text = "".join(stream_text_chunks)
                    p_tok = count_tokens(prompt_text, model_name)
                    c_tok = count_tokens(response_text, model_name)
                    logger.info(
                        f"Stream usage missing — estimated {p_tok}+{c_tok} tokens "
                        f"for model={model_name} endpoint={endpoint_id}"
                    )
                if p_tok or c_tok:
                    real_cost = estimate_cost(model_name, p_tok, c_tok)
                    # Accumulate only the delta for this request; the rotator
                    # adds it atomically under budget_lock. No lock needed here
                    # because cost_ref is per-request and not shared.
                    cost_ref["delta"] = cost_ref.get("delta", 0.0) + real_cost
                    ctx.metadata["_stream_usage"] = {"prompt_tokens": p_tok, "completion_tokens": c_tok}
                    ctx.metadata["_stream_cost_usd"] = round(real_cost, 6)

                    # Charge budget atomically now that the stream is done.
                    # The rotator cannot do this because it runs before the
                    # generator; cost_ref["delta"] was still 0.0 at that point.
                    _budget_lock = cost_ref.get("_budget_lock")  # type: ignore[arg-type]
                    _rotator = cost_ref.get("_rotator")  # type: ignore[arg-type]
                    if _budget_lock and _rotator:
                        async with _budget_lock:  # type: ignore[attr-defined]
                            _rotator.total_cost_today += real_cost  # type: ignore[attr-defined]

                    # Log spend entry for streaming requests directly here,
                    # because chat.py cannot read response.body for streaming.
                    try:
                        import datetime as _dt
                        import time as _time
                        store = ctx.state.extra.get("store") if ctx.state else None
                        if store and hasattr(store, "log_spend"):
                            _now = int(_time.time())
                            _date = _dt.date.today().isoformat()
                            _key = ctx.metadata.get("_key_prefix", "")
                            _provider = ctx.metadata.get("_provider", "")
                            await store.log_spend(
                                ts=_now, date=_date, key_prefix=_key,
                                model=model_name, provider=_provider,
                                prompt_tokens=p_tok, completion_tokens=c_tok,
                                cost_usd=real_cost,
                                latency_ms=round(ctx.metadata.get("duration", 0) * 1000, 1),
                                status=200,
                            )
                    except Exception as e:
                        logger.debug(f"Stream spend log skipped: {e}")

        ctx.response = StreamingResponse(stream_generator(), media_type="text/event-stream")
        return ctx.response

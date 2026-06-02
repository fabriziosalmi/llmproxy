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


# Rolling window cap for the speculative-analyzer text buffer. A 128 KB
# window holds plenty of context for injection-pattern matching (patterns
# are short; the window only needs to be longer than the longest pattern
# plus typical chunk size). Without this cap, a 5 MB streaming response
# would buffer all 5 MB in RAM until the stream finished — bounded per-
# request OOM under streaming load. The total-char counter is preserved
# so missing-usage token estimation scales correctly for long responses.
_MAX_STREAM_BUFFER_CHARS = 131_072
_MAX_STREAM_HOLD_BYTES = 1_048_576  # 1 MiB hard-cap for buffered security gate


class _BoundedStreamBuffer:
    """Rolling-window text buffer for the speculative analyzer.

    Holds at most `max_chars` characters of recent stream text (the
    analyzer scans the recent suffix; older chunks are dropped). A
    separate `total_chars` counter records every character ever
    appended, so missing-usage token estimation can scale up from the
    sampled window: `tokens ≈ sample_tokens × total_chars / sample_chars`.

    Backed by a list[str] so existing consumers that do `"".join(buf.chunks)`
    keep working without an interface change.
    """

    __slots__ = ("chunks", "_buf_chars", "total_chars", "_max")

    def __init__(self, max_chars: int = _MAX_STREAM_BUFFER_CHARS):
        self.chunks: list[str] = []
        self._buf_chars: int = 0
        self.total_chars: int = 0
        self._max: int = max_chars

    def append(self, text: str) -> None:
        if not text:
            return
        self.chunks.append(text)
        n = len(text)
        self._buf_chars += n
        self.total_chars += n
        # Evict oldest chunks until within cap. Always keep at least one
        # chunk so the analyzer has the most-recent text to scan.
        while self._buf_chars > self._max and len(self.chunks) > 1:
            dropped = self.chunks.pop(0)
            self._buf_chars -= len(dropped)

    @property
    def buf_chars(self) -> int:
        return self._buf_chars

    def text(self) -> str:
        return "".join(self.chunks)


# Actionable hints surfaced in 4xx/quota error details so operators reading
# the audit log or SDK error don't have to guess where to fix the key.
_PROVIDER_HINTS = {
    "openai": (
        "Check key at https://platform.openai.com/api-keys; "
        "billing/credits at https://platform.openai.com/account/billing"
    ),
    "anthropic": (
        "Check key at https://console.anthropic.com/settings/keys; "
        "billing at https://console.anthropic.com/settings/billing"
    ),
    "google": "Check key at https://aistudio.google.com/app/apikey",
    "azure": "Check the Azure OpenAI resource → Keys and Endpoint in https://portal.azure.com",
    "groq": "Check key at https://console.groq.com/keys",
    "mistral": "Check key at https://console.mistral.ai/api-keys/",
    "openrouter": "Check key/credits at https://openrouter.ai/keys",
    "cohere": "Check key at https://dashboard.cohere.com/api-keys",
}


def _actionable_hint(provider: str | None, status_code: int) -> str:
    """Return a hint suffix for known auth/quota failures, '' otherwise."""
    if status_code not in (401, 402, 403, 429):
        return ""
    p = (provider or "").lower()
    if p in _PROVIDER_HINTS:
        return f" — {_PROVIDER_HINTS[p]}"
    return ""


@dataclass
class ForwardingContext:
    """Decouples the forwarder from the orchestrator's internals.

    Instead of passing raw locks, callbacks, and object references,
    the orchestrator builds this context once and hands it over.
    """

    config: dict = field(default_factory=dict)
    config_provider: Callable[[], dict] | None = None
    circuit_manager: Any = None
    budget_lock: asyncio.Lock | None = None
    get_session: Callable[[], Awaitable[Any]] | None = None
    add_log: Callable[..., Awaitable[None]] | None = None
    security: Any = None  # SecurityShield for mid-stream PII/injection monitoring


class RequestForwarder:
    """Forwards requests to upstream LLM providers with fallback chain support.

    Config sourcing: callers may pass either a static `config` dict (legacy,
    no hot-reload) or a `config_provider` callable that returns the live agent
    config on every read. The provider is the source of truth when both are
    given — it's how the orchestrator wires hot-reload through. Without this,
    rebinding `agent.config = new_dict` in the watcher silently leaves the
    forwarder stuck on the boot-time config.
    """

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
        config_provider: Callable[[], dict] | None = None,
    ):
        if ctx is not None:
            self._static_config = ctx.config
            self._config_provider = ctx.config_provider
            self.circuit_manager = ctx.circuit_manager
            self._budget_lock = ctx.budget_lock
            self._get_session = ctx.get_session
            self._add_log = ctx.add_log
            self._security = ctx.security
        else:
            self._static_config = config or {}
            self._config_provider = config_provider
            self.circuit_manager = circuit_manager
            self._budget_lock = budget_lock
            self._get_session = get_session
            self._add_log = add_log
            self._security = security

    @property
    def config(self) -> dict:
        """Always returns the live config. Reads via provider when wired,
        else falls back to the static dict passed at construction."""
        return self._live_config()

    def _live_config(self) -> dict:
        if self._config_provider is not None:
            try:
                cfg = self._config_provider()
                if cfg is not None:
                    return cfg
            except Exception:
                # Defensive: provider failure shouldn't 500 the request path.
                # Fall back to the last-known static config.
                logger.debug(
                    "Config provider read failed; using static config", exc_info=True
                )
        return self._static_config

    def resolve_endpoint_for_provider(self, provider: str) -> Any:
        """Resolve the configured endpoint URL for a provider."""
        endpoints_cfg = self._live_config().get("endpoints", {})
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

    async def forward_request(
        self,
        ctx,
        adapter,
        target_url,
        translated_body,
        translated_headers,
        session,
        cb,
        endpoint_id,
    ):
        """Forward a single request (non-streaming) with circuit breaker tracking."""
        response = await adapter.request(
            target_url, translated_body, translated_headers, session
        )
        if response.status_code in (429, 500, 502, 503, 504):
            await cb.report_failure()
            # Provider hint surfaces an actionable next-step (key dashboard /
            # billing) on 429 — rate-limited or quota-exhausted is the most
            # common operator-fixable upstream error. 4xx auth (401/403)
            # passes through to the SDK caller unchanged so existing client
            # error-handling paths still work.
            provider = getattr(
                ctx.metadata.get("target_endpoint"), "provider", None
            ) or getattr(ctx.metadata.get("target_endpoint"), "provider_type", None)
            hint = _actionable_hint(provider, response.status_code)
            raise HTTPException(
                status_code=response.status_code,
                detail=f"Upstream {endpoint_id} returned {response.status_code}{hint}",
            )
        await cb.report_success()
        return response

    async def forward_with_fallback(
        self, ctx, target, headers, session, cost_ref: "dict[str, Any] | None" = None
    ):
        """Forward request with cross-provider fallback on failure.

        Tries the primary endpoint first. On failure (circuit open, HTTP error,
        connection error), walks the fallback_chain for the requested model.
        """
        from .adapters.registry import get_adapter

        original_model = ctx.body.get("model", "")
        original_body = dict(ctx.body)
        attempts = []

        is_budget_saturated = ctx.metadata.get("_budget_saturated", False)

        # Build attempt list: primary + fallback chain
        primary_provider = getattr(target, "provider", None) or getattr(
            target, "provider_type", None
        )
        primary_adapter = get_adapter(primary_provider, original_model)

        if not is_budget_saturated:
            attempts.append(
                {
                    "target": target,
                    "adapter": primary_adapter,
                    "model": original_model,
                    "provider": primary_adapter.provider_name,
                    "is_fallback": False,
                }
            )

        # Add fallback chain entries (read live so config hot-reloads apply)
        chain = self._live_config().get("fallback_chains", {}).get(original_model, [])
        for fb in chain:
            fb_target = self.resolve_endpoint_for_provider(fb["provider"])
            if fb_target:
                fb_adapter = get_adapter(fb["provider"])
                attempts.append(
                    {
                        "target": fb_target,
                        "adapter": fb_adapter,
                        "model": fb["model"],
                        "provider": fb["provider"],
                        "is_fallback": True,
                    }
                )

        if not attempts:
            if is_budget_saturated:
                raise HTTPException(status_code=402, detail="Enterprise Quota Exceeded for this API Key (No Fallback Available)")
            raise HTTPException(status_code=503, detail="No routable endpoints available")

        last_error: Exception | None = None
        for i, attempt in enumerate(attempts):
            a_target = attempt["target"]
            a_adapter = attempt["adapter"]
            a_model = attempt["model"]
            endpoint_id = (
                getattr(a_target, "id", str(a_target.url)) if a_target else "unknown"
            )

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
            ep_metadata = getattr(a_target, "metadata", {}) or {}
            api_key_env = ep_metadata.get("api_key_env", "")
            if api_key_env:
                import os

                api_key = os.environ.get(api_key_env, "")
                if api_key:
                    provider_headers["Authorization"] = f"Bearer {api_key}"

            # Translate request for this provider
            target_url, translated_body, translated_headers = (
                a_adapter.translate_request(
                    str(a_target.url),
                    ctx.body,
                    provider_headers,
                )
            )

            try:
                if ctx.body.get("stream") or original_body.get("stream"):
                    return await self._handle_streaming(
                        ctx,
                        a_adapter,
                        target_url,
                        translated_body,
                        translated_headers,
                        session,
                        cb,
                        endpoint_id,
                        cost_ref=cost_ref,
                    )
                else:
                    ctx.response = await self.forward_request(
                        ctx,
                        a_adapter,
                        target_url,
                        translated_body,
                        translated_headers,
                        session,
                        cb,
                        endpoint_id,
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

    async def _handle_streaming(
        self,
        ctx,
        adapter,
        target_url,
        translated_body,
        translated_headers,
        session,
        cb,
        endpoint_id,
        cost_ref: "dict[str, Any] | None" = None,
    ):
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
        # Bounded rolling-window buffer (caps memory for long streams).
        stream_buf = _BoundedStreamBuffer()
        # Backwards-compatible alias for the analyzer's existing list-based
        # interface. analyze_speculative reads via "".join(stream_chunks).
        stream_text_chunks = stream_buf.chunks
        kill_event = asyncio.Event()
        speculative_task: asyncio.Task | None = None
        if self._security:
            prompt = ""
            messages = ctx.body.get("messages", [])
            if messages:
                prompt = str(messages[-1].get("content", ""))
            speculative_task = asyncio.create_task(
                self._security.analyze_speculative(
                    prompt, stream_text_chunks, kill_event
                )
            )
        sec_cfg = self._live_config().get("security", {}) or {}
        gate_cfg = sec_cfg.get("streaming_buffered_gate", {}) or {}
        gate_enabled = bool(gate_cfg.get("enabled", False))
        gate_tenants = gate_cfg.get("tenants", ["*"]) or ["*"]
        tenant_id = (
            ctx.metadata.get("_cache_tenant")
            or ctx.metadata.get("_key_prefix")
            or ctx.session_id
            or "default"
        )
        tenant_match = "*" in gate_tenants or tenant_id in gate_tenants
        buffered_gate = gate_enabled and tenant_match
        hold_limit = int(gate_cfg.get("max_buffer_bytes", _MAX_STREAM_HOLD_BYTES))
        held_chunks: list[bytes] = []
        held_bytes = 0

        async def stream_generator():
            nonlocal first_chunk_seen, circuit_success_reported, held_bytes
            stream_usage = {}
            try:
                async for chunk in adapter.stream(
                    target_url, translated_body, translated_headers, session
                ):
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
                            for line in chunk.decode("utf-8", errors="replace").split(
                                "\n"
                            ):
                                if (
                                    line.startswith("data: ")
                                    and line[6:].strip() != "[DONE]"
                                ):
                                    d = json.loads(line[6:])
                                    u = d.get("usage") or d.get("usageMetadata", {})
                                    if u:
                                        stream_usage = u
                        except (json.JSONDecodeError, KeyError, UnicodeDecodeError):
                            logger.debug(
                                "Stream usage chunk parse skipped", exc_info=True
                            )
                    # Feed decoded text to the speculative analyzer via the
                    # bounded rolling-window buffer.
                    if speculative_task is not None:
                        try:
                            stream_buf.append(chunk.decode("utf-8", errors="replace"))
                        except Exception:
                            logger.debug("Stream buffer append skipped", exc_info=True)
                    if buffered_gate:
                        held_chunks.append(chunk)
                        held_bytes += len(chunk)
                        if held_bytes > hold_limit:
                            logger.warning(
                                "Buffered gate overflow (%s bytes > %s) for tenant=%s; "
                                "failing closed",
                                held_bytes,
                                hold_limit,
                                tenant_id,
                            )
                            yield (
                                b'data: {"error":"stream_buffer_overflow",'
                                b'"message":"Buffered security gate overflow"}\n\n'
                            )
                            return
                    else:
                        yield chunk
                if buffered_gate:
                    # Release buffered chunks only after full upstream completion
                    # and after speculative guardrail had the full response.
                    if kill_event.is_set():
                        yield (
                            b'data: {"error":"stream_blocked",'
                            b'"message":"Response blocked by content policy"}\n\n'
                        )
                        return
                    for c in held_chunks:
                        yield c
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
                    p_tok = stream_usage.get("prompt_tokens") or stream_usage.get(
                        "promptTokenCount", 0
                    )
                    c_tok = stream_usage.get("completion_tokens") or stream_usage.get(
                        "candidatesTokenCount", 0
                    )
                else:
                    # Fallback: estimate tokens from accumulated text when
                    # provider omits usage chunk (prevents budget bypass).
                    prompt_text = " ".join(
                        str(m.get("content", "")) for m in ctx.body.get("messages", [])
                    )
                    p_tok = count_tokens(prompt_text, model_name)
                    sample_text = stream_buf.text()
                    sample_tok = count_tokens(sample_text, model_name)
                    # Scale up if the bounded buffer dropped earlier chunks:
                    # token rate per char is ~uniform within a single response,
                    # so total ≈ sample × (total_chars / sample_chars).
                    if sample_text and stream_buf.total_chars > len(sample_text):
                        scale = stream_buf.total_chars / max(1, len(sample_text))
                        c_tok = int(sample_tok * scale)
                    else:
                        c_tok = sample_tok
                    logger.info(
                        f"Stream usage missing — estimated {p_tok}+{c_tok} tokens "
                        f"for model={model_name} endpoint={endpoint_id} "
                        f"(buf={len(sample_text)}/total={stream_buf.total_chars})"
                    )
                if p_tok or c_tok:
                    real_cost = estimate_cost(model_name, p_tok, c_tok)
                    # Accumulate only the delta for this request; the rotator
                    # adds it atomically under budget_lock. No lock needed here
                    # because cost_ref is per-request and not shared.
                    cost_ref["delta"] = cost_ref.get("delta", 0.0) + real_cost
                    ctx.metadata["_stream_usage"] = {
                        "prompt_tokens": p_tok,
                        "completion_tokens": c_tok,
                    }
                    ctx.metadata["_stream_cost_usd"] = round(real_cost, 6)

                    # Charge budget atomically + persist. The rotator cannot
                    # do this earlier because it runs before the generator —
                    # cost_ref["delta"] was still 0.0 at that point and
                    # chat.py's post-call enqueue ran before this finally
                    # block fires.
                    _budget_lock = cost_ref.get("_budget_lock")
                    _rotator = cost_ref.get("_rotator")
                    if _budget_lock and _rotator:
                        from .budget import charge_and_persist

                        await charge_and_persist(_rotator, _budget_lock, real_cost)

                    # Log spend + audit for streaming requests directly here.
                    # chat.py / completions.py cannot read response.body for
                    # streaming, so the forwarder is the only chokepoint that
                    # has both the real token counts AND sees every route
                    # (chat, completions legacy, embeddings if they ever stream).
                    import datetime as _dt
                    import time as _time
                    from core.metrics import MetricsTracker as _MT

                    store = ctx.state.extra.get("store") if ctx.state else None
                    if store and hasattr(store, "log_spend"):
                        _now = int(_time.time())
                        _date = _dt.date.today().isoformat()
                        _key = ctx.metadata.get("_key_prefix", "")
                        _provider = ctx.metadata.get("_provider", "")
                        _req_id = ctx.metadata.get("req_id", "")
                        _session = (getattr(ctx, "session_id", "") or "")[:16]
                        _latency_ms = round(ctx.metadata.get("duration", 0) * 1000, 1)
                        try:
                            await store.log_spend(
                                ts=_now,
                                date=_date,
                                key_prefix=_key,
                                model=model_name,
                                provider=_provider,
                                prompt_tokens=p_tok,
                                completion_tokens=c_tok,
                                cost_usd=real_cost,
                                latency_ms=_latency_ms,
                                status=200,
                            )
                        except Exception as e:
                            logger.warning(f"Stream spend log failed: {e}")
                        if hasattr(store, "log_audit"):
                            try:
                                await store.log_audit(
                                    ts=_now,
                                    req_id=_req_id,
                                    session_id=_session,
                                    key_prefix=_key,
                                    model=model_name,
                                    provider=_provider,
                                    status=200,
                                    prompt_tokens=p_tok,
                                    completion_tokens=c_tok,
                                    cost_usd=real_cost,
                                    latency_ms=_latency_ms,
                                    metadata="{}",
                                )
                                _MT.track_audit_persistence("forwarder_stream", "ok")
                            except Exception as e:
                                _MT.track_audit_persistence("forwarder_stream", "fail")
                                logger.warning(f"Stream audit log failed: {e}")

        ctx.response = StreamingResponse(
            stream_generator(), media_type="text/event-stream"
        )
        return ctx.response

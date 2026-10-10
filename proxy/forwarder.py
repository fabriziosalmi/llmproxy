"""
LLMPROXY — Request Forwarder.

Handles upstream forwarding with cross-provider fallback, circuit breaker
integration, streaming support, and post-stream budget charging.
"""

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import aiohttp
from fastapi import HTTPException
from fastapi.responses import Response, StreamingResponse

from core.metrics import MetricsTracker
from core.tool_policy import ToolPolicy, called_tools, follows_tool_result

from .adapters.base import UpstreamStatusError
from .adapters.sse import SSEReassembler

logger = logging.getLogger("llmproxy.forwarder")

#: Strong references to in-flight stream accounting tasks (an unreferenced task
#: can be collected mid-run).
_STREAM_FINALIZERS: set[asyncio.Task] = set()


def _log_finalizer_failure(task: asyncio.Task) -> None:
    if not task.cancelled() and task.exception() is not None:
        logger.warning("Stream accounting failed: %s", task.exception())


#: Upstream statuses that count against the endpoint and send the request to
#: the next provider. Everything else is the caller's answer and is relayed.
_RETRYABLE_UPSTREAM_STATUSES = frozenset({429, 500, 502, 503, 504})


class UpstreamReadTimeout(HTTPException):
    """The upstream took the request and did not answer in time.

    Not sent to the next provider. A connection that could not be made cost
    nothing and is worth retrying elsewhere; a request that was delivered is
    most likely still being generated, and billed, so sending it again makes
    the caller wait twice and pay twice for an answer that was already late.
    """


def _is_read_timeout(exc: BaseException) -> bool:
    if isinstance(exc, getattr(aiohttp, "ConnectionTimeoutError", ())):
        return False  # never connected: nothing was sent
    return isinstance(exc, TimeoutError)


def _endpoint_provider(endpoint: Any) -> str | None:
    """The provider an upstream belongs to, read the same way everywhere.

    A stored endpoint (LLMEndpoint) answers from metadata["provider"]; the
    SimpleNamespace stand-ins built for fallback-chain entries carry
    ``provider`` and the legacy ``provider_type``.
    """
    return getattr(endpoint, "provider", None) or getattr(
        endpoint, "provider_type", None
    )


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


class _StreamObserver:
    """Reads a stream as events, for accounting and for the mid-stream guard.

    The bytes sent to the client are not touched; this only looks at them. It
    replaces two things that worked on raw TCP reads:

    * The usage record was parsed out of whichever read contained the string
      ``"usage"``; when that event straddled two reads it was lost and the
      request was billed on an estimate.
    * The estimate, and the guard, were fed the raw stream:
      ``data: {"id":...,"choices":[{"delta":{"content":"Hi"}}]}`` rather than
      ``Hi``. Counting tokens over the framing overstated the completion 40 to
      100 times, enough for a few dozen streamed answers without a usage record
      to exhaust the daily budget; and a phrase split across deltas was never
      contiguous in what the guard scanned, so it matched nothing.

    What the model wrote (``delta.content``, legacy ``text``, tool-call names
    and arguments) goes into ``buf``. An event that is not in the chat-chunk
    shape is appended as it came, so an unfamiliar stream is still counted and
    scanned, conservatively.
    """

    __slots__ = ("_events", "_buf", "_usage", "_tools")

    def __init__(self, buf: "_BoundedStreamBuffer", usage: dict[str, Any]):
        self._events = SSEReassembler()
        self._buf = buf
        self._usage = usage
        self._tools: list[str] = []

    def take_tool_names(self) -> list[str]:
        """The tools the stream has started to call since this was last asked."""
        names, self._tools = self._tools, []
        return names

    def feed(self, chunk: bytes) -> None:
        for event in self._events.feed(chunk):
            self._observe(event)

    def finish(self) -> None:
        tail = self._events.flush()
        if tail:
            self._observe(tail)

    def _observe(self, event: bytes) -> None:
        for raw in event.split(b"\n"):
            if not raw.startswith(b"data:"):
                continue
            payload = raw[5:].strip()
            if not payload or payload == b"[DONE]":
                continue
            try:
                data = json.loads(payload)
            except ValueError:
                self._buf.append(payload.decode("utf-8", errors="replace"))
                continue
            if not isinstance(data, dict):
                continue
            usage = data.get("usage") or data.get("usageMetadata")
            if isinstance(usage, dict) and usage:
                self._usage.clear()
                self._usage.update(usage)
            choices = data.get("choices")
            if isinstance(choices, list):
                for choice in choices:
                    if isinstance(choice, dict):
                        self._buf.append(_choice_text(choice))
                        self._tools.extend(called_tools(choice.get("delta")))
            elif not usage:
                self._buf.append(payload.decode("utf-8", errors="replace"))


def _choice_text(choice: dict[str, Any]) -> str:
    """What one stream choice adds to the answer: text and tool-call fragments."""
    delta = choice.get("delta")
    parts: list[str] = []
    if isinstance(delta, dict):
        content = delta.get("content")
        if isinstance(content, str):
            parts.append(content)
        for call in delta.get("tool_calls") or []:
            fn = call.get("function") if isinstance(call, dict) else None
            if isinstance(fn, dict):
                parts.append(str(fn.get("name") or ""))
                parts.append(str(fn.get("arguments") or ""))
    text = choice.get("text")
    if isinstance(text, str):
        parts.append(text)
    return "".join(parts)


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
                    # The provider's key is looked up through this, as it is
                    # for a stored endpoint. Without it a fallback attempt went
                    # out with no credential at all: falling back to any
                    # provider that needs a key ended in its 401.
                    metadata={
                        "provider": provider,
                        "api_key_env": ep_config.get("api_key_env", ""),
                        "models": ep_config.get("models", []),
                    },
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
        try:
            response = await adapter.request(
                target_url, translated_body, translated_headers, session
            )
        except (TimeoutError, aiohttp.ClientError, OSError) as e:
            # The breaker hears about this. It did not: a network error or a
            # timeout left here without a report, so a dead endpoint never
            # opened its circuit from non-streaming traffic, and when the
            # request was the half-open probe the probe was never given back.
            await cb.report_failure()
            if _is_read_timeout(e):
                raise UpstreamReadTimeout(
                    status_code=504,
                    detail=f"Upstream {endpoint_id} did not answer in time",
                ) from e
            raise
        if response.status_code in _RETRYABLE_UPSTREAM_STATUSES:
            await cb.report_failure()
            # Provider hint surfaces an actionable next-step (key dashboard /
            # billing) on 429 — rate-limited or quota-exhausted is the most
            # common operator-fixable upstream error. 4xx auth (401/403)
            # passes through to the SDK caller unchanged so existing client
            # error-handling paths still work.
            provider = _endpoint_provider(ctx.metadata.get("target_endpoint"))
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

        if is_budget_saturated:
            raise HTTPException(
                status_code=402,
                detail="FinOps: Budget Exceeded (HTTP 402). Silent downgrades are disabled to preserve strict downstream parsing logic."
            )

        # Build attempt list: primary + fallback chain
        primary_provider = _endpoint_provider(target)
        primary_adapter = get_adapter(primary_provider, original_model)

        # Only if routing actually selected one. `target` arrives from
        # ctx.metadata.get("target_endpoint"), which is None when the ROUTING
        # ring picked nothing — an empty pool, every endpoint gated, or the
        # routing plugin disabled. This used to be appended unconditionally,
        # so the "no routable endpoints" guard below could never fire: the
        # list always held one entry, the walk reached `a_target.url` and
        # raised AttributeError, and the caller got a generic 502 "Upstream
        # request failed" instead of a 503 naming the actual cause.
        if target is not None:
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
            raise HTTPException(status_code=503, detail="No routable endpoints available")

        last_error: Exception | None = None
        for _i, attempt in enumerate(attempts):
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

            except UpstreamReadTimeout:
                ctx.body["model"] = original_model
                raise
            except (TimeoutError, aiohttp.ClientError, OSError) as e:
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

        # Open the upstream and read its first chunk *before* building the
        # response. Once a StreamingResponse is returned its 200 status line is
        # committed, so an upstream 429/503 discovered afterwards could only be
        # relayed as stream content: the client saw a success, the circuit
        # breaker saw a successful first chunk, and the fallback chain was never
        # tried. Failing here lets forward_with_fallback treat a stream exactly
        # like a non-streaming call.
        upstream = adapter.stream(target_url, translated_body, translated_headers, session)
        try:
            first_upstream_chunk = await anext(upstream, None)
        except UpstreamStatusError as e:
            if e.status in _RETRYABLE_UPSTREAM_STATUSES:
                await cb.report_failure()
                provider = _endpoint_provider(ctx.metadata.get("target_endpoint"))
                hint = _actionable_hint(provider, e.status)
                raise HTTPException(
                    status_code=e.status,
                    detail=f"Upstream {endpoint_id} returned {e.status}{hint}",
                ) from e
            # Any other status passes through with the upstream's own body, as
            # it does for a non-streaming call (a 401 or 400 is the caller's).
            # The endpoint answered, which is what the breaker asks: without
            # this a half-open probe that got a 4xx was never given back.
            await cb.report_success()
            ctx.response = Response(
                content=e.content, status_code=e.status, media_type=e.media_type
            )
            return ctx.response
        except (TimeoutError, aiohttp.ClientError, OSError, RuntimeError):
            await cb.report_failure()
            raise

        async def upstream_chunks():
            try:
                if first_upstream_chunk is not None:
                    yield first_upstream_chunk
                async for chunk in upstream:
                    yield chunk
            finally:
                await upstream.aclose()

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

        stream_usage: dict[str, Any] = {}
        observer = _StreamObserver(stream_buf, stream_usage)
        tool_policy = ToolPolicy.from_config(self._live_config())
        after_data = tool_policy.enabled and follows_tool_result(ctx.body.get("messages"))
        # How the stream ended, for the spend/audit rows and the outcome counter.
        # The status line already said 200, so this is the only place the truth
        # is kept: a guardrail kill, an upstream failure mid-stream and a client
        # abort were all recorded as an ordinary 200.
        outcome: dict[str, Any] = {"status": 200, "blocked": False, "reason": ""}

        async def _finalize_stream():
            """Charge, log and count a finished stream.

            Runs as a task of its own (see the generator's ``finally``): a client
            that disconnects cancels the generator, and the first real await in
            this block (the budget lock, the spend and audit writes) would be
            interrupted, so abandoned streams were charged to the in-memory
            budget, which does not suspend when uncontended, but never written to
            the spend ledger or the audit chain.
            """
            if outcome["blocked"]:
                label = "blocked"
            elif outcome["status"] == 499:
                label = "client_disconnect"
            elif outcome["status"] >= 500:
                label = "upstream_error"
            else:
                label = "completed"
            MetricsTracker.track_stream_outcome(label)

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
                # Fallback: estimate tokens from the text the model wrote
                # (see _StreamObserver) when the provider sends no usage
                # record, so a stream without one is still charged.
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
                # The non-streaming routes feed these counters from the response
                # body; a stream never has one, so without this the token and
                # cost series (and the cost-per-hour alert and top-models panel
                # built on them) saw none of the streaming traffic.
                from core.model_resolver import known_model_names

                MetricsTracker.track_usage(
                    endpoint=ctx.metadata.get("_route", "/v1/chat/completions"),
                    model=model_name,
                    prompt_tokens=p_tok,
                    completion_tokens=c_tok,
                    cost=real_cost,
                    known_models=known_model_names(self._live_config()),
                )
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
                            status=outcome["status"],
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
                                status=outcome["status"],
                                prompt_tokens=p_tok,
                                completion_tokens=c_tok,
                                cost_usd=real_cost,
                                latency_ms=_latency_ms,
                                blocked=outcome["blocked"],
                                block_reason=outcome["reason"],
                                metadata="{}",
                            )
                            _MT.track_audit_persistence("forwarder_stream", "ok")
                        except Exception as e:
                            _MT.track_audit_persistence("forwarder_stream", "fail")
                            logger.warning(f"Stream audit log failed: {e}")
        async def stream_generator():
            nonlocal first_chunk_seen, circuit_success_reported, held_bytes
            try:
                async for chunk in upstream_chunks():
                    # Abort stream if speculative guardrail fired
                    if kill_event.is_set():
                        logger.warning(
                            f"STREAM ABORTED by speculative guardrail (endpoint={endpoint_id})"
                        )
                        outcome.update(blocked=True, reason="stream_blocked")
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
                    # The usage record, and the text the model wrote: for the
                    # accounting when no usage record comes, and for the guard.
                    try:
                        observer.feed(chunk)
                    except Exception:
                        logger.debug("Stream observation skipped", exc_info=True)
                    # The tool policy, as the call appears. The chunk that
                    # completes the call's name is not sent on: the client is
                    # left with an unfinished call it cannot execute, and an
                    # error event saying why.
                    refused = None
                    if tool_policy.enabled:
                        for name in observer.take_tool_names():
                            reason = tool_policy.refusal(name, after_data)
                            if reason is None:
                                continue
                            if tool_policy.enforce:
                                refused = (name, reason)
                                break
                            MetricsTracker.track_tool_policy("would_refuse")
                            logger.warning(
                                "TOOL POLICY (log only): call to '%s' would be refused: %s",
                                name, reason,
                            )
                    if refused is not None:
                        MetricsTracker.track_tool_policy("refused")
                        logger.warning("TOOL POLICY: call to '%s' refused: %s", *refused)
                        outcome.update(blocked=True, reason=f"tool_refused:{refused[0]}"[:200])
                        yield (
                            b'data: {"error":"tool_refused",'
                            b'"message":"Tool call refused by policy"}\n\n'
                        )
                        return
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
                            outcome.update(
                                blocked=True, reason="stream_buffer_overflow"
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
                        outcome.update(blocked=True, reason="stream_blocked")
                        yield (
                            b'data: {"error":"stream_blocked",'
                            b'"message":"Response blocked by content policy"}\n\n'
                        )
                        return
                    for c in held_chunks:
                        yield c
            except (TimeoutError, OSError, RuntimeError, aiohttp.ClientError) as e:
                outcome.update(status=502, reason="upstream_error")
                if not circuit_success_reported:
                    await cb.report_failure()
                raise e
            except (asyncio.CancelledError, GeneratorExit):
                # The client went away: the response already said 200, but the
                # record should say how it ended.
                outcome.update(status=499, reason="client_disconnect")
                raise
            finally:
                try:
                    observer.finish()
                except Exception:
                    logger.debug("Stream observation skipped", exc_info=True)
                # Signal speculative task to stop and cancel if still running
                kill_event.set()
                if speculative_task is not None and not speculative_task.done():
                    speculative_task.cancel()
                # Account in a task of its own and wait for it shielded: when the
                # client disconnects this generator is cancelled, and the first
                # real await in an inline block would be interrupted. The task
                # carries on without us; a module-level set keeps it alive.
                task = asyncio.ensure_future(_finalize_stream())
                _STREAM_FINALIZERS.add(task)
                task.add_done_callback(_STREAM_FINALIZERS.discard)
                task.add_done_callback(_log_finalizer_failure)
                await asyncio.shield(task)

        ctx.response = StreamingResponse(
            stream_generator(), media_type="text/event-stream"
        )
        return ctx.response

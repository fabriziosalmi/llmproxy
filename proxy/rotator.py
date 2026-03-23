"""
LLMProxy — Security Gateway Orchestrator.

RotatorAgent is the core orchestrator: initializes the security pipeline,
wires route modules via the app factory, and handles the proxy request chain
through the 5-ring plugin system with SecurityShield pre-inspection.
"""

import os
import json
import uuid
import yaml  # type: ignore[import-untyped]
import asyncio
import logging
import uvicorn
import aiohttp
import time
from datetime import datetime
from typing import Optional, Dict, Any

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from core.base_agent import BaseAgent
from core.metrics import MetricsTracker
from core.tracing import TraceManager
from core.zero_trust import ZeroTrustManager
from core.rbac import RBACManager
from core.circuit_breaker import CircuitManager
from core.security import SecurityShield
from core.secrets import SecretManager
from core.firewall_asgi import ByteLevelFirewallMiddleware
from core.plugin_engine import PluginManager, PluginHook, PluginContext, PluginState
from core.identity import IdentityManager
from core.webhooks import WebhookDispatcher, EventType
from core.export import DatasetExporter
from core.cache import CacheBackend, NegativeCache
from core.stream_faker import fake_stream

from store.base import BaseRepository
from .adapters.registry import get_adapter

logger = logging.getLogger("llmproxy.rotator")


def create_app(agent) -> FastAPI:
    """App factory: builds the FastAPI application with middleware and routes."""
    from core.rate_limiter import RateLimitMiddleware

    app = FastAPI(title="LLMPROXY")
    TraceManager.instrument_app(app)

    # Security + observability response headers
    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        # W3C Trace Context: propagate trace ID for full-stack observability
        trace_id = request.headers.get("x-trace-id") or request.headers.get("traceparent", "").split("-")[1] if "-" in request.headers.get("traceparent", "") else None
        if trace_id:
            response.headers["X-Trace-Id"] = trace_id
        if request.url.path.startswith("/ui"):
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; "
                "script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
                "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
                "font-src 'self' https://fonts.gstatic.com; "
                "img-src 'self' data:; "
                "connect-src 'self'; "
                "frame-ancestors 'none'"
            )
        return response

    cors_origins = agent.config.get("server", {}).get("cors_origins", ["*"])
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
    )
    app.add_middleware(ByteLevelFirewallMiddleware)
    app.add_middleware(RateLimitMiddleware, config=agent.config)

    from .routes import (
        admin_router, registry_router, identity_router,
        plugins_router, telemetry_router, chat_router,
        models_router, embeddings_router, completions_router,
    )
    app.include_router(chat_router(agent))
    app.include_router(completions_router(agent))
    app.include_router(embeddings_router(agent))
    app.include_router(models_router(agent))
    app.include_router(admin_router(agent))
    app.include_router(registry_router(agent))
    app.include_router(identity_router(agent))
    app.include_router(plugins_router(agent))
    app.include_router(telemetry_router(agent))

    # Serve frontend
    ui_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ui")
    if os.path.exists(ui_path):
        app.mount("/ui", StaticFiles(directory=ui_path, html=True), name="ui")

    @app.on_event("shutdown")
    async def _shutdown():
        await agent._drain_pending_writes()
        # Force SQLite WAL checkpoint before container receives SIGKILL
        try:
            import aiosqlite
            async with aiosqlite.connect(agent.store.db_path) as conn:
                await conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except Exception:
            pass
        await agent.cache_backend.close()

    return app


class RotatorAgent(BaseAgent):
    """Security gateway orchestrator — routes requests through the plugin pipeline."""

    def __init__(self, store: BaseRepository, assistant=None, config_path: str = "config.yaml"):
        super().__init__("rotator")
        self._session: Optional[aiohttp.ClientSession] = None
        self.store = store
        self.config_path = config_path
        self.model_adapter = get_adapter("openai")  # default, overridden per-request
        self.config = self._load_config()
        self._config_hash = self._compute_config_hash_sync()

        # Security subsystems
        self.security = SecurityShield(self.config, assistant=assistant)
        self.zt_manager = ZeroTrustManager(self.config)
        self.rbac = RBACManager()
        self.identity = IdentityManager(self.config)
        self.circuit_manager = CircuitManager(on_state_change=self._on_circuit_state_change)

        # Alerting & compliance
        self.webhooks = WebhookDispatcher(self.config)
        export_cfg = self.config.get("observability", {}).get("export", {})
        self.exporter = DatasetExporter(
            output_dir=export_cfg.get("output_dir", "exports"),
            scrub=export_cfg.get("scrub_pii", True),
            compress_on_rotate=export_cfg.get("compress_on_rotate", True),
        ) if export_cfg.get("enabled") else None

        # Budget tracking (hydrated from SQLite in setup())
        self.total_cost_today = 0.0
        self._budget_date: str | None = None
        self._budget_lock = asyncio.Lock()
        self._pending_writes: asyncio.Queue = asyncio.Queue(maxsize=500)

        # Gateway state
        self.proxy_enabled = True
        self.priority_mode = False
        self.features = {
            "language_guard": True,
            "injection_guard": True,
            "link_sanitizer": True,
        }
        self.log_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=100)
        self.telemetry_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1000)

        # L1: Negative cache (in-memory WAF drop)
        neg_cfg = self.config.get("caching", {}).get("negative_cache", {})
        self.negative_cache = NegativeCache(
            maxsize=neg_cfg.get("maxsize", 50_000),
            ttl=neg_cfg.get("ttl", 300),
            enabled=self.config.get("caching", {}).get("enabled", True),
        )

        # L2: Positive cache backend (WAF-aware exact-match)
        cache_cfg = self.config.get("caching", {})
        self.cache_backend = CacheBackend(
            db_path=cache_cfg.get("db_path", "cache.db"),
            ttl=cache_cfg.get("ttl", 3600),
            enabled=cache_cfg.get("enabled", True),
        )

        # Request deduplication (idempotency key support)
        from core.deduplicator import RequestDeduplicator
        self.deduplicator = RequestDeduplicator(ttl_seconds=300)

        # Plugin engine
        self.plugin_manager = PluginManager()
        self.plugin_state = PluginState(
            cache=self.cache_backend,
            metrics=MetricsTracker,
            config=self.config.get("plugins", {}),
            extra={"store": self.store},
        )

        self.app = create_app(self)

    # ── Config & Secrets ──

    def _load_config(self):
        if os.path.exists(self.config_path):
            with open(self.config_path, 'r') as f:
                return yaml.safe_load(f)
        return {"server": {"auth": {"enabled": False}}}

    def _compute_config_hash_sync(self) -> str:
        """Blocking hash — must run via to_thread() from async context."""
        import hashlib
        if os.path.exists(self.config_path):
            with open(self.config_path, 'rb') as f:
                return hashlib.md5(f.read()).hexdigest()
        return ""

    async def _config_watch_loop(self, interval: int = 30):
        """Background loop: detect config.yaml changes and hot-reload."""
        while True:
            await asyncio.sleep(interval)
            try:
                new_hash = await asyncio.to_thread(self._compute_config_hash_sync)
                if new_hash and new_hash != self._config_hash:
                    self.config = self._load_config()
                    self._config_hash = new_hash
                    self.webhooks = WebhookDispatcher(self.config)
                    self.security = SecurityShield(self.config)
                    self.logger.info("Config hot-reloaded (file change detected)")
            except Exception as e:
                self.logger.warning(f"Config watch error: {e}")

    async def _write_flush_loop(self, interval: float = 1.0):
        """Background loop: flush pending state writes to SQLite periodically."""
        while True:
            await asyncio.sleep(interval)
            await self._drain_pending_writes()

    async def _drain_pending_writes(self):
        """Drain all pending writes from the queue to the store."""
        writes: list[tuple[str, Any]] = []
        while not self._pending_writes.empty():
            try:
                writes.append(self._pending_writes.get_nowait())
            except asyncio.QueueEmpty:
                break
        for key, value in writes:
            try:
                await self.store.set_state(key, value)
            except Exception as e:
                self.logger.warning(f"Failed to flush state write {key}: {e}")

    def enqueue_write(self, key: str, value: Any):
        """Non-blocking enqueue of a state write. Drops silently if queue is full."""
        try:
            self._pending_writes.put_nowait((key, value))
        except asyncio.QueueFull:
            self.logger.warning("Pending writes queue full, dropping write")

    def _get_api_keys(self) -> list[str]:
        env_var = self.config.get("server", {}).get("auth", {}).get("api_keys_env", "LLM_PROXY_API_KEYS")
        raw = SecretManager.get_secret(env_var, "") or ""
        return [k.strip() for k in raw.split(",") if k.strip()]

    # ── HTTP Session ──

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            # Configurable connection pooling for upstream providers
            http_cfg = self.config.get("server", {})
            timeout_s = int(http_cfg.get("timeout", "30s").rstrip("s"))
            connector = aiohttp.TCPConnector(
                limit=100,           # max total connections
                limit_per_host=20,   # max per upstream provider
                ttl_dns_cache=300,   # DNS cache TTL (5 min)
                enable_cleanup_closed=True,
            )
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(
                    total=timeout_s,
                    sock_connect=10,   # TCP connect timeout (detect half-open)
                    sock_read=timeout_s,  # Socket read timeout
                ),
                connector=connector,
            )
        return self._session

    # ── Circuit Breaker Callbacks ──

    def _on_circuit_state_change(self, endpoint: str, old_state: str, new_state: str):
        MetricsTracker.set_circuit_state(endpoint, new_state == "open")
        if new_state == "open":
            asyncio.create_task(self.webhooks.dispatch(
                EventType.CIRCUIT_OPEN, {"endpoint": endpoint, "from": old_state, "to": new_state}
            ))
        elif old_state == "open" and new_state == "closed":
            asyncio.create_task(self.webhooks.dispatch(
                EventType.ENDPOINT_RECOVERED, {"endpoint": endpoint}
            ))

    # ── Logging & Telemetry ──

    async def _add_log(self, message: str, level: str = "INFO", metadata: dict | None = None, trace_id: str | None = None):
        entry: Dict[str, Any] = {
            "timestamp": time.strftime("%H:%M:%S"),
            "level": level,
            "message": message,
            "metadata": metadata or {},
        }
        if trace_id:
            entry["trace_id"] = trace_id
        if self.log_queue.full():
            dropped = self.log_queue.get_nowait()
            self._dlq_write(dropped)
        await self.log_queue.put(entry)

    async def broadcast_event(self, event_type: str, data: Dict[str, Any]):
        event = {"type": event_type, "timestamp": datetime.now().isoformat(), "data": data}
        if self.telemetry_queue.full():
            dropped = self.telemetry_queue.get_nowait()
            self._dlq_write(dropped)
        await self.telemetry_queue.put(event)

    def _dlq_write(self, entry: Any):
        """Dead-letter queue: persist dropped log/telemetry entries to file.
        Non-blocking, best-effort -- prevents silent data loss under load spikes."""
        try:
            with open("dlq.jsonl", "a") as f:
                f.write(json.dumps(entry, default=str) + "\n")
        except Exception:
            pass  # DLQ is best-effort, never block the hot path

    # ── Lifecycle ──

    async def setup(self):
        """Pre-flight: DB init, plugin load, state hydration, cache init."""
        await self.store.init()
        await self.cache_backend.init()
        await self.plugin_manager.load_plugins()

        # Hydrate persisted state
        self.proxy_enabled = await self.store.get_state("proxy_enabled", True)
        self.priority_mode = await self.store.get_state("priority_mode", False)
        for f in self.features:
            self.features[f] = await self.store.get_state(f"feature_{f}", self.features[f])
            self.security.config[f] = {"enabled": self.features[f]}

        # Budget hydration (daily reset)
        import datetime as _dt
        today = _dt.date.today().isoformat()
        saved_date = await self.store.get_state("budget:daily_date", None)
        if saved_date == today:
            self.total_cost_today = await self.store.get_state("budget:daily_total", 0.0)
        else:
            self.total_cost_today = 0.0
            await self.store.set_state("budget:daily_date", today)
            await self.store.set_state("budget:daily_total", 0.0)
        self._budget_date = today

        # Background cache eviction loop
        eviction_interval = self.config.get("caching", {}).get("eviction_interval", 3600)
        if self.cache_backend._enabled:
            asyncio.create_task(self._cache_eviction_loop(eviction_interval))

        # Background config watcher (hot-reload)
        asyncio.create_task(self._config_watch_loop(30))

        # Background write flusher (budget persistence, graceful shutdown safe)
        asyncio.create_task(self._write_flush_loop(1.0))

        # Active health probing (background endpoint liveness checks)
        from core.health_prober import EndpointHealthProber
        self._health_prober = EndpointHealthProber(self.config, self.circuit_manager, self._get_session)
        asyncio.create_task(self._health_prober.start())

        # Background deduplicator cleanup (prevent memory leak from expired entries)
        asyncio.create_task(self._dedup_cleanup_loop(60))

        self.logger.info("Security gateway ready.")

    async def _dedup_cleanup_loop(self, interval: int = 60):
        """Periodically clean expired entries from the request deduplicator."""
        while True:
            await asyncio.sleep(interval)
            try:
                self.deduplicator.cleanup_expired()
            except Exception as e:
                self.logger.debug(f"Dedup cleanup error: {e}")

    async def run(self, port: int | None = None):
        if port is None:
            port = self.config.get("server", {}).get("port", 8090)
        host = self.config.get("server", {}).get("host", "0.0.0.0")
        await self.setup()
        config = uvicorn.Config(self.app, host=host, port=port, log_level="info")
        server = uvicorn.Server(config)
        await server.serve()

    # ── Cache Eviction ──

    async def _cache_eviction_loop(self, interval: int = 3600):
        """Background loop: evict expired cache entries periodically."""
        while True:
            await asyncio.sleep(interval)
            try:
                deleted = await self.cache_backend.evict_expired()
                if deleted > 0:
                    self.logger.info(f"Cache eviction: {deleted} entries purged")
            except Exception as e:
                self.logger.error(f"Cache eviction error: {e}")

    # ── Forwarding with cross-provider fallback ──

    def _resolve_endpoint_for_provider(self, provider: str) -> Any:
        """Resolve the configured endpoint URL for a provider."""
        endpoints_cfg = self.config.get("endpoints", {})
        for ep_name, ep_config in endpoints_cfg.items():
            if ep_config.get("provider") == provider or ep_name == provider:
                base_url = ep_config.get("base_url", "")
                # Build a lightweight endpoint-like object
                from types import SimpleNamespace
                return SimpleNamespace(
                    id=ep_name,
                    url=base_url,
                    provider=provider,
                    provider_type=provider,
                )
        return None

    async def _forward_request(self, ctx, adapter, target_url, translated_body, translated_headers, session, cb, endpoint_id):
        """Forward a single request (non-streaming) with circuit breaker tracking."""
        response = await adapter.request(target_url, translated_body, translated_headers, session)
        # Check for HTTP-level errors that should trigger fallback
        if response.status_code in (429, 500, 502, 503, 504):
            cb.report_failure()
            raise HTTPException(status_code=response.status_code, detail=f"Upstream {endpoint_id} returned {response.status_code}")
        cb.report_success()
        return response

    async def _forward_with_fallback(self, ctx, target, headers, session):
        """Forward request with cross-provider fallback on failure.

        Tries the primary endpoint first. On failure (circuit open, HTTP error,
        connection error), walks the fallback_chain for the requested model.
        """
        original_model = ctx.body.get("model", "")
        original_body = dict(ctx.body)  # shallow copy for fallback restoration
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
            fb_target = self._resolve_endpoint_for_provider(fb["provider"])
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
                    continue  # skip this fallback, try next
                # Primary circuit open — fall through to fallbacks
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
                    # Streaming: return generator (no fallback mid-stream)
                    ttft_start = time.perf_counter()
                    first_chunk_seen = False
                    circuit_success_reported = False

                    async def stream_generator(_adapter=a_adapter, _url=target_url,
                                               _body=translated_body, _headers=translated_headers,
                                               _cb=cb, _eid=endpoint_id):
                        nonlocal first_chunk_seen, circuit_success_reported
                        stream_usage = {}
                        try:
                            async for chunk in _adapter.stream(_url, _body, _headers, session):
                                if not first_chunk_seen:
                                    first_chunk_seen = True
                                    _cb.report_success()
                                    circuit_success_reported = True
                                    ttft = time.perf_counter() - ttft_start
                                    MetricsTracker.track_ttft(_eid, ttft)
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
                                    except Exception:
                                        pass
                                yield chunk
                        except Exception as e:
                            if not circuit_success_reported:
                                _cb.report_failure()
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
                                async with self._budget_lock:
                                    self.total_cost_today += real_cost
                                ctx.metadata["_stream_usage"] = {"prompt_tokens": p_tok, "completion_tokens": c_tok}
                                ctx.metadata["_stream_cost_usd"] = round(real_cost, 6)

                    ctx.response = StreamingResponse(stream_generator(), media_type="text/event-stream")
                    return ctx.response
                else:
                    ctx.response = await self._forward_request(
                        ctx, a_adapter, target_url, translated_body, translated_headers,
                        session, cb, endpoint_id,
                    )
                    return ctx.response

            except Exception as e:
                last_error = e
                if not attempt["is_fallback"]:
                    await self._add_log(
                        f"PRIMARY FAILED: {endpoint_id} — {e}", level="PROXY",
                    )
                continue

        # All attempts exhausted
        ctx.body["model"] = original_model  # restore original model
        if last_error:
            raise last_error
        raise HTTPException(status_code=503, detail="All providers failed")

    # ── Core Proxy Pipeline ──

    async def proxy_request(self, request: Request, body: Dict[str, Any] | None = None, session_id: str = "default"):
        start_total = time.time()
        if body is None:
            body = await request.json()

        ctx = PluginContext(
            request=request,
            body=body,
            session_id=session_id,
            metadata={
                "rotator": self,
                "req_id": uuid.uuid4().hex[:8],
                "_cache_control": request.headers.get("cache-control", "") if request else "",
            },
            state=self.plugin_state,
        )

        try:
            # L1: Negative Cache — drop repeated attacks in <0.1ms
            neg_reason = self.negative_cache.check(ctx.body)
            if neg_reason:
                logger.debug(f"L1 Negative Cache drop: {neg_reason[:50]}")
                MetricsTracker.track_injection_blocked()
                raise HTTPException(status_code=403, detail=neg_reason)

            # Pre-ring: SecurityShield (injection scoring + trajectory analysis)
            security_error = self.security.inspect(ctx.body, session_id)
            if security_error:
                logger.warning(f"SecurityShield blocked: {security_error}")
                MetricsTracker.track_injection_blocked()
                # Write to L1 negative cache for future instant drops
                self.negative_cache.add(ctx.body, security_error)
                raise HTTPException(status_code=403, detail=security_error)

            # RING 1: INGRESS (Auth, ZT, Rate Limit)
            r1_start = time.perf_counter()
            await self.plugin_manager.execute_ring(PluginHook.INGRESS, ctx)
            MetricsTracker.track_ring_latency("ingress", time.perf_counter() - r1_start)
            if ctx.stop_chain:
                MetricsTracker.track_injection_blocked()
                asyncio.create_task(self.webhooks.dispatch(
                    EventType.INJECTION_BLOCKED, {"reason": ctx.error or "Ingress Blocked", "session": session_id[:8]}
                ))
                raise HTTPException(status_code=403, detail=ctx.error or "Ingress Blocked")

            # RING 2: PRE-FLIGHT (PII masking, budget guard, loop breaker, cache lookup)
            r2_start = time.perf_counter()
            await self.plugin_manager.execute_ring(PluginHook.PRE_FLIGHT, ctx)
            MetricsTracker.track_ring_latency("pre_flight", time.perf_counter() - r2_start)
            if ctx.stop_chain:
                # Cache hit: if client wants streaming, convert cached response to SSE
                if ctx.metadata.get("_cache_hit") and ctx.body.get("stream"):
                    cached_data = ctx.metadata.get("_cached_response_data")
                    if cached_data:
                        ctx.response = StreamingResponse(
                            fake_stream(cached_data), media_type="text/event-stream",
                            headers={"X-LLMProxy-Cache": "HIT"},
                        )
                return ctx.response

            # Model alias/group resolution (before routing)
            from core.model_resolver import resolve_model
            original_model = ctx.body.get("model", "")
            resolved_model = resolve_model(self.config, original_model)
            if resolved_model != original_model:
                ctx.body["model"] = resolved_model
                ctx.metadata["_model_alias"] = original_model

            # RING 3: ROUTING
            r3_start = time.perf_counter()
            await self.plugin_manager.execute_ring(PluginHook.ROUTING, ctx)
            MetricsTracker.track_ring_latency("routing", time.perf_counter() - r3_start)
            if ctx.stop_chain:
                raise HTTPException(status_code=503, detail=ctx.error or "No Routing Target")

            target = ctx.metadata.get("target_endpoint")
            headers = ctx.body.get("headers", {})
            headers.update(self.zt_manager.get_identity_headers())

            # Forward request with cross-provider fallback
            start_req = time.time()
            session = await self._get_session()
            await self._forward_with_fallback(ctx, target, headers, session)

            ctx.metadata["duration"] = time.time() - start_req

            # Update endpoint performance stats for smart routing
            from plugins.default.neural_router import update_endpoint_stats
            routed_endpoint_id = getattr(
                ctx.metadata.get("target_endpoint"), 'id',
                ctx.metadata.get("_provider", "unknown"),
            )
            success = ctx.response and hasattr(ctx.response, "status_code") and ctx.response.status_code < 400
            update_endpoint_stats(routed_endpoint_id, ctx.metadata["duration"] * 1000, bool(success))

            # RING 4: POST-FLIGHT (response sanitization, watermarking)
            r4_start = time.perf_counter()
            await self.plugin_manager.execute_ring(PluginHook.POST_FLIGHT, ctx)
            MetricsTracker.track_ring_latency("post_flight", time.perf_counter() - r4_start)
            if ctx.stop_chain:
                return JSONResponse(content={"error": ctx.error}, status_code=403)

            # RING 5: BACKGROUND (telemetry, export, cache write)
            async def _bg_ring():
                r5_start = time.perf_counter()
                await self.plugin_manager.execute_ring(PluginHook.BACKGROUND, ctx)
                MetricsTracker.track_ring_latency("background", time.perf_counter() - r5_start)

                # Fase 3: Post-flight validated cache write
                # Only cache if:
                #   1. Cache key was computed (miss on lookup)
                #   2. Response exists and is not a streaming response
                #   3. POST_FLIGHT didn't block (no [SEC_ERR:])
                #   4. Not a cache bypass request
                cache_key = ctx.metadata.get("_cache_key")
                if (
                    cache_key
                    and self.cache_backend._enabled
                    and not ctx.metadata.get("_cache_bypass")
                    and ctx.response
                    and hasattr(ctx.response, "body")
                    and not ctx.metadata.get("_cache_hit")
                ):
                    try:
                        response_data = json.loads(ctx.response.body.decode())
                        # Verify POST_FLIGHT didn't flag security issues
                        content = response_data.get("choices", [{}])[0].get("message", {}).get("content", "")
                        if "[SEC_ERR:" not in content:
                            await self.cache_backend.put(
                                body=ctx.body,
                                response_data=response_data,
                                tenant_id=ctx.metadata.get("_cache_tenant", ctx.session_id),
                                model=ctx.body.get("model", ""),
                            )
                    except Exception as e:
                        logger.debug(f"Cache write skipped: {e}")

            asyncio.create_task(_bg_ring())

            # Inject proxy metadata headers on responses
            if ctx.response and hasattr(ctx.response, "headers"):
                cache_status = ctx.metadata.get("_cache_status", "")
                if cache_status:
                    ctx.response.headers["X-LLMProxy-Cache"] = cache_status
                ctx.response.headers["X-LLMProxy-Provider"] = ctx.metadata.get("_provider", "")
                ctx.response.headers["X-LLMProxy-Request-Id"] = ctx.metadata.get("req_id", "")

            # Store total pipeline latency in trace (O(1) via index dict)
            total_ms = (time.time() - start_total) * 1000
            req_id = ctx.metadata.get("req_id", "unknown")
            trace = self.plugin_manager._ring_traces_index.get(req_id)
            if trace:
                trace["total_ms"] = round(total_ms, 2)
                trace["upstream_ms"] = round(ctx.metadata.get("duration", 0) * 1000, 2)
                if "ttft_ms" in ctx.metadata:
                    trace["ttft_ms"] = ctx.metadata["ttft_ms"]

            return ctx.response

        except HTTPException:
            raise
        except Exception as e:
            self.logger.error(f"Proxy pipeline error: {e}")
            TraceManager.capture_exception(e)
            raise HTTPException(status_code=502, detail="Upstream request failed")

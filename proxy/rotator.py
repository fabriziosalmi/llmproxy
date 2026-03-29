"""
LLMProxy — Security Gateway Orchestrator.

RotatorAgent is the core orchestrator: initializes the security pipeline,
wires route modules via the app factory, and handles the proxy request chain
through the 5-ring plugin system with SecurityShield pre-inspection.

Extracted modules:
  - proxy/app_factory.py   — FastAPI app + middleware + routes
  - proxy/event_log.py     — Log/telemetry queues + DLQ
  - proxy/background.py    — Background loops (config watch, write flush, cache eviction)
  - proxy/forwarder.py     — Upstream forwarding + fallback chain
"""

import os
import json
import uuid
import yaml
import asyncio
import logging
import uvicorn
import aiohttp
import time
from typing import Optional, Dict, Any

from fastapi import HTTPException
from fastapi.responses import JSONResponse, StreamingResponse

from core.base_agent import BaseAgent
from core.metrics import MetricsTracker
from core.tracing import TraceManager
from core.zero_trust import ZeroTrustManager
from core.rbac import RBACManager
from core.circuit_breaker import CircuitManager
from core.security import SecurityShield
from core.secrets import SecretManager
from core.plugin_engine import PluginManager, PluginHook, PluginContext, PluginState
from core.identity import IdentityManager
from core.webhooks import WebhookDispatcher, EventType
from core.export import DatasetExporter
from core.cache import CacheBackend, NegativeCache
from core.stream_faker import fake_stream

from core.model_resolver import resolve_model
from plugins.default.neural_router import update_endpoint_stats

from store.base import BaseRepository
from .adapters.registry import get_adapter
from .app_factory import create_app
from .event_log import EventLogger
from .forwarder import RequestForwarder

logger = logging.getLogger("llmproxy.rotator")


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
        self._session_lock = asyncio.Lock()
        self._pending_writes: asyncio.Queue = asyncio.Queue(maxsize=500)

        # Strong references for background tasks (prevents GC in Python 3.12+)
        self._background_tasks: set[asyncio.Task] = set()

        # Gateway state
        self.proxy_enabled = True
        self.priority_mode = False
        self.features = {
            "language_guard": True,
            "injection_guard": True,
            "link_sanitizer": True,
        }

        # Event logging (extracted to proxy/event_log.py)
        self._event_logger = EventLogger()
        self.log_queue = self._event_logger.log_queue          # backward compat
        self.telemetry_queue = self._event_logger.telemetry_queue  # backward compat

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

        # Response signing (S2: cryptographic provenance)
        from core.response_signer import ResponseSigner
        signing_cfg = self.config.get("security", {}).get("response_signing", {})
        signing_key = signing_cfg.get("secret") or os.environ.get("LLM_PROXY_SIGNING_KEY", "")
        self.response_signer = ResponseSigner(signing_key)

        # Request forwarder (extracted to proxy/forwarder.py)
        self.forwarder = RequestForwarder(
            config=self.config,
            circuit_manager=self.circuit_manager,
            budget_lock=self._budget_lock,
            get_session=self._get_session,
            add_log=self._add_log,
            security=self.security,
        )

        self.app = create_app(self)

    # ── Task Management ──

    def _spawn_task(self, coro) -> asyncio.Task:
        """Create a background task with a strong reference to prevent GC."""
        task = asyncio.create_task(coro)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return task

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

    def enqueue_write(self, key: str, value: Any):
        """Non-blocking enqueue of a state write. Logs error if queue is full."""
        try:
            self._pending_writes.put_nowait((key, value))
        except asyncio.QueueFull:
            self.logger.error("Pending writes queue full — budget state write DROPPED")

    async def flush_budget_now(self):
        """Immediate flush of pending writes — called on critical budget thresholds."""
        from .background import drain_pending_writes
        await drain_pending_writes(self)

    def _get_api_keys(self) -> list[str]:
        env_var = self.config.get("server", {}).get("auth", {}).get("api_keys_env", "LLM_PROXY_API_KEYS")
        raw = SecretManager.get_secret(env_var, "") or ""
        return [k.strip() for k in raw.split(",") if k.strip()]

    # ── HTTP Session ──

    async def _get_session(self) -> aiohttp.ClientSession:
        # Fast path: session already alive — no lock needed
        if self._session is not None and not self._session.closed:
            return self._session
        # Slow path: create session under lock to prevent duplicate connectors
        async with self._session_lock:
            # Re-check after acquiring lock (another coroutine may have created it)
            if self._session is not None and not self._session.closed:
                return self._session
            http_cfg = self.config.get("server", {})
            timeout_s = int(http_cfg.get("timeout", "30s").rstrip("s"))
            pool_cfg = self.config.get("connection_pool", {})
            connector = aiohttp.TCPConnector(
                limit=pool_cfg.get("max_connections", 100),
                limit_per_host=pool_cfg.get("max_per_host", 30),
                ttl_dns_cache=pool_cfg.get("dns_cache_ttl", 300),
                enable_cleanup_closed=True,
                keepalive_timeout=pool_cfg.get("keepalive_timeout", 30),
            )
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(
                    total=timeout_s,
                    sock_connect=pool_cfg.get("connect_timeout", 10),
                    sock_read=timeout_s,
                ),
                connector=connector,
            )
            self.logger.info(
                f"HTTP pool: max={connector.limit} per_host={connector.limit_per_host} "
                f"keepalive={pool_cfg.get('keepalive_timeout', 30)}s"
            )
            return self._session

    # ── Circuit Breaker Callbacks ──

    def _on_circuit_state_change(self, endpoint: str, old_state: str, new_state: str):
        MetricsTracker.set_circuit_state(endpoint, new_state == "open")
        if new_state == "open":
            self._spawn_task(self.webhooks.dispatch(
                EventType.CIRCUIT_OPEN, {"endpoint": endpoint, "from": old_state, "to": new_state}
            ))
        elif old_state == "open" and new_state == "closed":
            self._spawn_task(self.webhooks.dispatch(
                EventType.ENDPOINT_RECOVERED, {"endpoint": endpoint}
            ))

    # ── Logging (delegates to EventLogger) ──

    async def _add_log(self, message: str, level: str = "INFO",
                       metadata: dict | None = None, trace_id: str | None = None):
        await self._event_logger.add_log(message, level, metadata, trace_id)

    async def broadcast_event(self, event_type: str, data: Dict[str, Any]):
        await self._event_logger.broadcast_event(event_type, data)

    # ── Lifecycle ──

    async def setup(self):
        """Pre-flight: DB init, plugin load, state hydration, cache init."""
        from .background import (
            config_watch_loop, write_flush_loop,
            cache_eviction_loop, dedup_cleanup_loop,
            retention_purge_loop,
        )

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

        # Background loops (extracted to proxy/background.py)
        eviction_interval = self.config.get("caching", {}).get("eviction_interval", 3600)
        if self.cache_backend._enabled:
            self._spawn_task(cache_eviction_loop(self.cache_backend, eviction_interval))
        self._spawn_task(config_watch_loop(self, 30))
        self._spawn_task(write_flush_loop(self, 0.25))

        # Active health probing
        from core.health_prober import EndpointHealthProber
        self._health_prober = EndpointHealthProber(self.config, self.circuit_manager, self._get_session)
        self._spawn_task(self._health_prober.start())

        # Dedup cleanup
        self._spawn_task(dedup_cleanup_loop(self.deduplicator, 60))

        # GDPR: automatic data retention purge
        gdpr_cfg = self.config.get("gdpr", {})
        if gdpr_cfg.get("auto_purge", True):
            retention_days = gdpr_cfg.get("retention_days", 90)
            self._spawn_task(retention_purge_loop(self.store, retention_days))

        self.logger.info("Security gateway ready.")

    async def run(self, port: int | None = None):
        if port is None:
            port = self.config.get("server", {}).get("port", 8090)
        host = self.config.get("server", {}).get("host", "0.0.0.0")
        await self.setup()
        config = uvicorn.Config(self.app, host=host, port=port, log_level="info")
        server = uvicorn.Server(config)
        await server.serve()

    # ── Core Proxy Pipeline ──

    async def proxy_request(self, request, body: Dict[str, Any] | None = None, session_id: str = "default"):
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

            # Pre-ring: SecurityShield (injection scoring + trajectory + cross-session)
            client_ip = request.client.host if hasattr(request, 'client') and request.client else ""
            key_prefix = session_id[:8] if session_id != "default" else ""
            security_error = await self.security.inspect(
                ctx.body, session_id, ip=client_ip, key_prefix=key_prefix,
            )
            if security_error:
                logger.warning(f"SecurityShield blocked: {security_error}")
                MetricsTracker.track_injection_blocked()
                self.negative_cache.add(ctx.body, security_error)
                raise HTTPException(status_code=403, detail=security_error)

            # RING 1: INGRESS (Auth, ZT, Rate Limit)
            r1_start = time.perf_counter()
            await self.plugin_manager.execute_ring(PluginHook.INGRESS, ctx)
            MetricsTracker.track_ring_latency("ingress", time.perf_counter() - r1_start)
            if ctx.stop_chain:
                MetricsTracker.track_injection_blocked()
                self._spawn_task(self.webhooks.dispatch(
                    EventType.INJECTION_BLOCKED, {"reason": ctx.error or "Ingress Blocked", "session": session_id[:8]}
                ))
                raise HTTPException(status_code=403, detail=ctx.error or "Ingress Blocked")

            # RING 2: PRE-FLIGHT (PII masking, budget guard, loop breaker, cache lookup)
            r2_start = time.perf_counter()
            await self.plugin_manager.execute_ring(PluginHook.PRE_FLIGHT, ctx)
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
            resolved_model = resolve_model(self.config, original_model)
            if resolved_model != original_model:
                ctx.body["model"] = resolved_model
                ctx.metadata["_model_alias"] = original_model

            # Budget-aware model downgrade: if over hard limit, fall back to local
            budget_cfg = self.config.get("budget", {})
            if budget_cfg.get("fallback_to_local_on_limit"):
                daily_limit = budget_cfg.get("daily_limit", 50.0)
                async with self._budget_lock:
                    over_budget = self.total_cost_today >= daily_limit
                if over_budget:
                    local_model = budget_cfg.get("local_model", "ollama/llama3.3")
                    ctx.metadata["_budget_downgrade"] = True
                    ctx.metadata["_original_model_pre_downgrade"] = ctx.body.get("model", "")
                    ctx.body["model"] = local_model
                    await self._add_log(
                        f"BUDGET DOWNGRADE: {ctx.body.get('model')} → {local_model} "
                        f"(${self.total_cost_today:.2f}/${daily_limit:.2f})",
                        level="PROXY",
                    )

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
            # Per-request delta dict: forwarder accumulates only the cost
            # increment for this request; rotator adds it atomically under
            # budget_lock, preventing lost-update when concurrent streams
            # each started from the same total_cost_today snapshot.
            cost_ref: dict[str, float] = {"delta": 0.0}
            await self.forwarder.forward_with_fallback(ctx, target, headers, session,
                                                       cost_ref=cost_ref)
            async with self._budget_lock:
                self.total_cost_today += cost_ref["delta"]

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
            await self.plugin_manager.execute_ring(PluginHook.POST_FLIGHT, ctx)
            MetricsTracker.track_ring_latency("post_flight", time.perf_counter() - r4_start)
            if ctx.stop_chain:
                return JSONResponse(content={"error": ctx.error}, status_code=403)

            # RING 5: BACKGROUND (telemetry, export, cache write)
            async def _bg_ring():
                r5_start = time.perf_counter()
                await self.plugin_manager.execute_ring(PluginHook.BACKGROUND, ctx)
                MetricsTracker.track_ring_latency("background", time.perf_counter() - r5_start)

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

            self._spawn_task(_bg_ring())

            # Inject proxy metadata headers on responses
            if ctx.response and hasattr(ctx.response, "headers"):
                cache_status = ctx.metadata.get("_cache_status", "")
                if cache_status:
                    ctx.response.headers["X-LLMProxy-Cache"] = cache_status
                ctx.response.headers["X-LLMProxy-Provider"] = ctx.metadata.get("_provider", "")
                ctx.response.headers["X-LLMProxy-Request-Id"] = ctx.metadata.get("req_id", "")

                # S2: Cryptographic response signing
                if self.response_signer.enabled and hasattr(ctx.response, "body"):
                    sig_headers = self.response_signer.sign_response(
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

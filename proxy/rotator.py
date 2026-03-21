"""
LLMProxy — Security Gateway Orchestrator.

RotatorAgent is the core orchestrator: initializes the security pipeline,
wires route modules via the app factory, and handles the proxy request chain
through the 5-ring plugin system with SecurityShield pre-inspection.
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
from core.trajectory import TrajectoryBuffer
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
    )
    app.include_router(chat_router(agent))
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

        # Security subsystems
        self.security = SecurityShield(self.config, assistant=assistant)
        self.zt_manager = ZeroTrustManager(self.config)
        self.rbac = RBACManager()
        self.identity = IdentityManager(self.config)
        self.circuit_manager = CircuitManager(on_state_change=self._on_circuit_state_change)
        self.trajectory = TrajectoryBuffer()

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
        self._budget_date = None

        # Gateway state
        self.proxy_enabled = True
        self.priority_mode = False
        self.features = {
            "language_guard": True,
            "injection_guard": True,
            "link_sanitizer": True,
        }
        self.log_queue = asyncio.Queue(maxsize=100)
        self.telemetry_queue = asyncio.Queue(maxsize=1000)

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

    def _get_api_keys(self) -> list[str]:
        env_var = self.config.get("server", {}).get("auth", {}).get("api_keys_env", "LLM_PROXY_API_KEYS")
        raw = SecretManager.get_secret(env_var, "")
        return [k.strip() for k in raw.split(",") if k.strip()]

    # ── HTTP Session ──

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30))
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

    async def _add_log(self, message: str, level: str = "INFO", metadata: dict = None):
        entry = {
            "timestamp": time.strftime("%H:%M:%S"),
            "level": level,
            "message": message,
            "metadata": metadata or {},
        }
        if self.log_queue.full():
            self.log_queue.get_nowait()
        await self.log_queue.put(entry)

    async def broadcast_event(self, event_type: str, data: Dict[str, Any]):
        event = {"type": event_type, "timestamp": datetime.now().isoformat(), "data": data}
        if self.telemetry_queue.full():
            self.telemetry_queue.get_nowait()
        await self.telemetry_queue.put(event)

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

        self.logger.info("Security gateway ready.")

    async def run(self, port: int = None):
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

    # ── Core Proxy Pipeline ──

    async def proxy_request(self, request: Request, body: Dict[str, Any] = None, session_id: str = "default"):
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

            # RING 3: ROUTING
            r3_start = time.perf_counter()
            await self.plugin_manager.execute_ring(PluginHook.ROUTING, ctx)
            MetricsTracker.track_ring_latency("routing", time.perf_counter() - r3_start)
            if ctx.stop_chain:
                raise HTTPException(status_code=503, detail=ctx.error or "No Routing Target")

            target = ctx.metadata.get("target_endpoint")
            headers = ctx.body.get("headers", {})
            headers.update(self.zt_manager.get_identity_headers())

            # Forward request
            start_req = time.time()
            session = await self._get_session()

            endpoint_id = getattr(target, 'id', str(target.url)) if target else 'unknown'

            # Resolve provider adapter: explicit config > model prefix auto-detect > openai default
            provider_type = getattr(target, 'provider', None) or getattr(target, 'provider_type', None)
            model_name = ctx.body.get("model", "")
            adapter = get_adapter(provider_type, model_name)
            ctx.metadata["_provider"] = adapter.provider_name

            # Translate request to provider-native format
            target_url, translated_body, translated_headers = adapter.translate_request(
                str(target.url), ctx.body, headers,
            )

            # Circuit breaker: check before forwarding, track outcome after
            cb = self.circuit_manager.get_breaker(endpoint_id)
            if not cb.can_execute():
                raise HTTPException(
                    status_code=503,
                    detail=f"Circuit open for endpoint '{endpoint_id}' — upstream unhealthy, retry later",
                )

            if ctx.body.get("stream"):
                ttft_start = time.perf_counter()
                first_chunk_seen = False
                circuit_success_reported = False

                async def stream_generator():
                    nonlocal first_chunk_seen, circuit_success_reported
                    try:
                        async for chunk in adapter.stream(target_url, translated_body, translated_headers, session):
                            if not first_chunk_seen:
                                first_chunk_seen = True
                                # First chunk received = upstream accepted the request
                                cb.report_success()
                                circuit_success_reported = True
                                ttft = time.perf_counter() - ttft_start
                                MetricsTracker.track_ttft(endpoint_id, ttft)
                                ctx.metadata["ttft_ms"] = round(ttft * 1000, 2)
                            yield chunk
                    except Exception as e:
                        if not circuit_success_reported:
                            cb.report_failure()
                        raise e
                ctx.response = StreamingResponse(stream_generator(), media_type="text/event-stream")
            else:
                try:
                    ctx.response = await adapter.request(target_url, translated_body, translated_headers, session)
                    cb.report_success()
                except Exception as e:
                    cb.report_failure()
                    raise e

            ctx.metadata["duration"] = time.time() - start_req

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

            # Inject cache status header on non-cached responses
            cache_status = ctx.metadata.get("_cache_status", "")
            if cache_status and ctx.response and hasattr(ctx.response, "headers"):
                ctx.response.headers["X-LLMProxy-Cache"] = cache_status

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

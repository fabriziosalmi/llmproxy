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

from store.base import BaseRepository
from .adapters.openai import OpenAIAdapter

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

    return app


class RotatorAgent(BaseAgent):
    """Security gateway orchestrator — routes requests through the plugin pipeline."""

    def __init__(self, store: BaseRepository, assistant=None, config_path: str = "config.yaml"):
        super().__init__("rotator")
        self._session: Optional[aiohttp.ClientSession] = None
        self.store = store
        self.config_path = config_path
        self.model_adapter = OpenAIAdapter()
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

        # Plugin engine
        self.plugin_manager = PluginManager()
        self.plugin_state = PluginState(
            cache={},
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
        async def _log():
            entry = {
                "timestamp": time.strftime("%H:%M:%S"),
                "level": level,
                "message": message,
                "metadata": metadata or {},
            }
            if self.log_queue.full():
                await self.log_queue.get()
            await self.log_queue.put(entry)
        await asyncio.shield(_log())

    async def broadcast_event(self, event_type: str, data: Dict[str, Any]):
        async def _put():
            event = {"type": event_type, "timestamp": datetime.now().isoformat(), "data": data}
            if self.telemetry_queue.full():
                self.telemetry_queue.get_nowait()
            await self.telemetry_queue.put(event)
        await asyncio.shield(_put())

    # ── Lifecycle ──

    async def setup(self):
        """Pre-flight: DB init, plugin load, state hydration."""
        await self.store.init()
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

        self.logger.info("Security gateway ready.")

    async def run(self, port: int = None):
        if port is None:
            port = self.config.get("server", {}).get("port", 8090)
        await self.setup()
        config = uvicorn.Config(self.app, host="0.0.0.0", port=port, log_level="info")
        server = uvicorn.Server(config)
        await server.serve()

    # ── Core Proxy Pipeline ──

    async def proxy_request(self, request: Request, body: Dict[str, Any] = None, session_id: str = "default"):
        start_total = time.time()
        if body is None:
            body = await request.json()

        ctx = PluginContext(
            request=request,
            body=body,
            session_id=session_id,
            metadata={"rotator": self, "req_id": uuid.uuid4().hex[:8]},
            state=self.plugin_state,
        )

        try:
            # Pre-ring: SecurityShield (injection scoring + trajectory analysis)
            security_error = self.security.inspect(ctx.body, session_id)
            if security_error:
                logger.warning(f"SecurityShield blocked: {security_error}")
                MetricsTracker.track_injection_blocked()
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

            # RING 2: PRE-FLIGHT (PII masking, budget guard, loop breaker)
            r2_start = time.perf_counter()
            await self.plugin_manager.execute_ring(PluginHook.PRE_FLIGHT, ctx)
            MetricsTracker.track_ring_latency("pre_flight", time.perf_counter() - r2_start)
            if ctx.stop_chain:
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

            if ctx.body.get("stream"):
                ttft_start = time.perf_counter()
                first_chunk_seen = False

                async def stream_generator():
                    nonlocal first_chunk_seen
                    async for chunk in self.model_adapter.stream(str(target.url), ctx.body, headers, session):
                        if not first_chunk_seen:
                            first_chunk_seen = True
                            ttft = time.perf_counter() - ttft_start
                            MetricsTracker.track_ttft(endpoint_id, ttft)
                            ctx.metadata["ttft_ms"] = round(ttft * 1000, 2)
                        yield chunk
                ctx.response = StreamingResponse(stream_generator(), media_type="text/event-stream")
            else:
                ctx.response = await self.model_adapter.request(str(target.url), ctx.body, headers, session)

            ctx.metadata["duration"] = time.time() - start_req

            # RING 4: POST-FLIGHT (response sanitization, watermarking)
            r4_start = time.perf_counter()
            await self.plugin_manager.execute_ring(PluginHook.POST_FLIGHT, ctx)
            MetricsTracker.track_ring_latency("post_flight", time.perf_counter() - r4_start)
            if ctx.stop_chain:
                return JSONResponse(content={"error": ctx.error}, status_code=403)

            # RING 5: BACKGROUND (telemetry, export)
            async def _bg_ring():
                r5_start = time.perf_counter()
                await self.plugin_manager.execute_ring(PluginHook.BACKGROUND, ctx)
                MetricsTracker.track_ring_latency("background", time.perf_counter() - r5_start)
            asyncio.create_task(_bg_ring())

            # Store total pipeline latency in trace
            total_ms = (time.time() - start_total) * 1000
            req_id = ctx.metadata.get("req_id", "unknown")
            for trace in reversed(self.plugin_manager._ring_traces):
                if trace.get("req_id") == req_id:
                    trace["total_ms"] = round(total_ms, 2)
                    trace["upstream_ms"] = round(ctx.metadata.get("duration", 0) * 1000, 2)
                    if "ttft_ms" in ctx.metadata:
                        trace["ttft_ms"] = ctx.metadata["ttft_ms"]
                    break

            return ctx.response

        except HTTPException:
            raise
        except Exception as e:
            self.logger.error(f"Proxy pipeline error: {e}")
            TraceManager.capture_exception(e)
            raise HTTPException(status_code=502, detail="Upstream request failed")

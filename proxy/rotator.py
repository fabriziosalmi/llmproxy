import os
import json
import uuid
import yaml
import asyncio
import logging
import uvicorn
import random
import aiohttp
import time
from datetime import datetime
from typing import Optional, Dict, Any

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from core.base_agent import BaseAgent
from models import EndpointStatus
from core.metrics import MetricsTracker
from core.tracing import TraceManager
from core.vllm_engine import VLLMEngine
from core.rate_limiter import DynamicRateLimiter
from core.semantic_router import SemanticRouter
from core.semantic_cache import SemanticCache
from core.mcp_hub import MCPHub
from core.load_predictor import LoadPredictor
from core.zero_trust import ZeroTrustManager
from core.federation import FederationManager
from core.rbac import RBACManager
from core.circuit_breaker import CircuitManager
from core.rl_rotator import RLRotator
from core.security import SecurityShield
from core.secrets import SecretManager
from core.trajectory import TrajectoryBuffer
from core.firewall_asgi import ByteLevelFirewallMiddleware
from core.plugin_engine import PluginManager, PluginHook, PluginContext, PluginState
from core.identity import IdentityManager
from core.webhooks import WebhookDispatcher, EventType
from core.export import DatasetExporter
from core.chatops import TelegramBot

from store.base import BaseRepository
from .adapters.openai import OpenAIAdapter

class RotatorAgent(BaseAgent):
    def __init__(self, store: BaseRepository, assistant=None, config_path: str = "config.yaml"):
        super().__init__("rotator")
        self._session: Optional[aiohttp.ClientSession] = None
        self.store = store
        self.config_path = config_path
        self.model_adapter = OpenAIAdapter() # Default adapter (Architect's Refinement)
        self.config = self._load_config()
        self.vllm = VLLMEngine(model_path=self.config.get("server", {}).get("vllm", {}).get("model_path"))
        self.limiter = DynamicRateLimiter()
        self.router = SemanticRouter(assistant=assistant)
        self.rl_rotator = RLRotator()
        self.circuit_manager = CircuitManager(on_state_change=self._on_circuit_state_change)
        
        # Initialize Semantic Cache
        cache_config = self.config.get("caching", {})
        if cache_config.get("enabled"):
            self.semantic_cache = SemanticCache(
                assistant=assistant,
                db_path=cache_config.get("db_path", "cache_db"),
                threshold=cache_config.get("threshold", 0.95)
            )
        else:
            self.semantic_cache = None
            
        # Initialize MCP Hub
        self.mcp_hub = MCPHub()
        
        # Initialize Load Predictor
        self.predictor = LoadPredictor()
        
        # Initialize Zero Trust Manager
        self.zt_manager = ZeroTrustManager(self.config)
        
        # Initialize Federation Manager
        self.federation = FederationManager(self.config)
        
        # Initialize RBAC Manager
        self.rbac = RBACManager()

        # Session 6: Initialize Identity Manager (SSO/OIDC)
        self.identity = IdentityManager(self.config)

        # Session A: Wire WebhookDispatcher (Slack/Teams/Discord alerts)
        self.webhooks = WebhookDispatcher(self.config)

        # Session A: Wire DatasetExporter (JSONL training data)
        export_cfg = self.config.get("observability", {}).get("export", {})
        self.exporter = DatasetExporter(
            output_dir=export_cfg.get("output_dir", "exports"),
            scrub=export_cfg.get("scrub_pii", True),
            compress_on_rotate=export_cfg.get("compress_on_rotate", True),
        ) if export_cfg.get("enabled") else None

        # Session A: Wire TelegramBot (ChatOps + HITL)
        self.chatbot = TelegramBot(self.config)

        self.total_cost_today = 0.0  # Hydrated from SQLite in setup() (J.5)
        self._budget_date = None     # Tracks current budget day for daily reset
        
        self.security = SecurityShield(self.config, assistant=assistant)
        self.proxy_enabled = True
        self.priority_mode = False
        self.features = {
            "language_guard": True,
            "injection_guard": True,
            "link_sanitizer": True
        }
        self.log_queue = asyncio.Queue(maxsize=100)
        self.telemetry_queue = asyncio.Queue(maxsize=1000)
        self.app = FastAPI(title="LLMPROXY")
        TraceManager.instrument_app(self.app)
        cors_origins = self.config.get("server", {}).get("cors_origins", ["*"])
        self.app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
        )
        
        # 10.1: Add Speculative Guardrails at the byte level
        self.app.add_middleware(ByteLevelFirewallMiddleware)
        
        # 10.2: Initialize Trajectory Memory
        self.plugin_manager = PluginManager()
        self.trajectory = TrajectoryBuffer()

        # Session I: PluginState DI — shared across all plugin contexts
        # J.5: store reference exposed via extra for budget persistence
        self.plugin_state = PluginState(
            cache={},
            metrics=MetricsTracker,
            config=self.config.get("plugins", {}),
            extra={"store": self.store},
        )
        
        self._setup_routes()
        self._setup_static()

    async def broadcast_event(self, event_type: str, data: Dict[str, Any]):
        """Broadcasts a real-time event to the telemetry queue (Shielded)."""
        async def _put():
            event = {
                "type": event_type,
                "timestamp": datetime.now().isoformat(),
                "data": data
            }
            if self.telemetry_queue.full():
                self.telemetry_queue.get_nowait() # Drop oldest
            await self.telemetry_queue.put(event)
        
        # 11.2: Protect telemetry from client disconnection aborts
        await asyncio.shield(_put())

    def _setup_static(self):
        # Serve the perfected cyber-dark UI
        ui_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ui")
        if os.path.exists(ui_path):
            self.app.mount("/ui", StaticFiles(directory=ui_path, html=True), name="ui")
            self.logger.info(f"UI mounted at /ui from {ui_path}")

    def _load_config(self):
        if os.path.exists(self.config_path):
            with open(self.config_path, 'r') as f:
                return yaml.safe_load(f)
        return {"server": {"auth": {"enabled": False}}}

    def _get_api_keys(self) -> list[str]:
        """Load API keys from environment variable (secure via SecretManager)."""
        env_var = self.config.get("server", {}).get("auth", {}).get("api_keys_env", "LLM_PROXY_API_KEYS")
        raw = SecretManager.get_secret(env_var, "")
        return [k.strip() for k in raw.split(",") if k.strip()]

    async def _get_session(self) -> aiohttp.ClientSession:
        """Returns a shared aiohttp session (singleton)."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30)
            )
        return self._session

    def _on_circuit_state_change(self, endpoint: str, old_state: str, new_state: str):
        """Session A+B: Callback when circuit breaker changes state."""
        MetricsTracker.set_circuit_state(endpoint, new_state == "open")
        if new_state == "open":
            asyncio.create_task(self.webhooks.dispatch(
                EventType.CIRCUIT_OPEN, {"endpoint": endpoint, "from": old_state, "to": new_state}
            ))
            asyncio.create_task(self.chatbot.notify_ops(
                f"⚡ *Circuit OPEN* for `{endpoint}` — requests blocked until recovery"
            ))
        elif old_state == "open" and new_state == "closed":
            asyncio.create_task(self.webhooks.dispatch(
                EventType.ENDPOINT_RECOVERED, {"endpoint": endpoint}
            ))

    def _setup_routes(self):
        """J.6 Refactor: Include sub-routers instead of defining 27 routes inline."""
        from .routes import (
            admin_router, registry_router, identity_router,
            plugins_router, telemetry_router, chat_router,
        )
        self.app.include_router(chat_router(self))
        self.app.include_router(admin_router(self))
        self.app.include_router(registry_router(self))
        self.app.include_router(identity_router(self))
        self.app.include_router(plugins_router(self))
        self.app.include_router(telemetry_router(self))

    async def _add_log(self, message: str, level: str = "INFO", metadata: dict = None):
        """Shielded logging to ensure IO completes even if client disconnects."""
        async def _log():
            log_entry = {
                "timestamp": time.strftime("%H:%M:%S"),
                "level": level,
                "message": message,
                "metadata": metadata or {}
            }
            if self.log_queue.full():
                await self.log_queue.get()
            await self.log_queue.put(log_entry)
            
        # 11.2: Protect log persistence from client disconnects
        await asyncio.shield(_log())

    async def setup(self):
        """Pre-flight setup: DB initialization and State hydration."""
        await self.store.init()
        await self.plugin_manager.load_plugins() # 13. Load Neural Plugins
        
        # Hydrate state
        self.proxy_enabled = await self.store.get_state("proxy_enabled", True)
        self.priority_mode = await self.store.get_state("priority_mode", False)
        for f in self.features:
            self.features[f] = await self.store.get_state(f"feature_{f}", self.features[f])
            self.security.config[f] = {"enabled": self.features[f]}

        # J.5: Hydrate budget from SQLite (daily reset on date change)
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

        self.logger.info("RotatorAgent state hydrated and Neural OS kernel ready.")

    async def run(self, port: int = None):
        if port is None:
            port = self.config.get("server", {}).get("port", 8000)
            
        await self.setup()
        self.logger.info(f"Starting proxy server on port {port}...")
        
        # Phase 11.1: System Limits Tuning (TCP_NODELAY & UVLoop for raw performance)
        config = uvicorn.Config(
            self.app, 
            host="0.0.0.0", 
            port=port, 
            log_level="info",
            loop="uvloop",     # Automatically disables Nagle's TCP_NODELAY buffering
            http="httptools"   # High-perf HTTP parsing
        )
        server = uvicorn.Server(config)
        await server.serve()

    async def proxy_request(self, request: Request, body: Dict[str, Any] = None, session_id: str = "default"):
        self.predictor.record_request()
        start_total = time.time()

        # Use pre-parsed body if provided, avoid double request.json() read
        if body is None:
            body = await request.json()

        # Initialize Plugin Context
        ctx = PluginContext(
            request=request,
            body=body,
            session_id=session_id,
            metadata={"rotator": self, "req_id": uuid.uuid4().hex[:8]},
            state=self.plugin_state,
        )

        try:
            # Pre-ring: SecurityShield inspection (session trajectory + injection scoring)
            security_error = self.security.inspect(ctx.body, session_id)
            if security_error:
                logger.warning(f"SecurityShield blocked: {security_error}")
                MetricsTracker.track_injection_blocked()
                raise HTTPException(status_code=403, detail=security_error)

            # RING 1: INGRESS (Auth, ZT, Rate Limit)
            await self.plugin_manager.execute_ring(PluginHook.INGRESS, ctx)
            if ctx.stop_chain:
                # Session A+B: Track injection blocks
                MetricsTracker.track_injection_blocked()
                asyncio.create_task(self.webhooks.dispatch(EventType.INJECTION_BLOCKED, {"reason": ctx.error or "Ingress Blocked", "session": session_id[:8]}))
                raise HTTPException(status_code=401 if not ctx.error else 403, detail=ctx.error or "Ingress Blocked")

            # RING 2: PRE-FLIGHT (PII, Context, Cache)
            await self.plugin_manager.execute_ring(PluginHook.PRE_FLIGHT, ctx)
            if ctx.stop_chain:
                return ctx.response # e.g. Cache Hit

            # RING 3: ROUTING (Model Selection)
            await self.plugin_manager.execute_ring(PluginHook.ROUTING, ctx)
            if ctx.stop_chain:
                raise HTTPException(status_code=503, detail=ctx.error or "No Routing Target")

            target = ctx.metadata.get("target_endpoint")
            headers = ctx.body.get("headers", {})
            headers.update(self.zt_manager.get_identity_headers())
            
            # 4. Neural Execution (Model Request)
            start_req = time.time()
            session = await self._get_session()
            
            if ctx.body.get("stream"):
                async def stream_generator():
                    # Stream metadata (plugin chain info)
                    plugin_names = [p.get("name", "unknown") for p in self.plugin_manager.rings[PluginHook.PRE_FLIGHT]]
                    yield f"data: {json.dumps({'object': 'proxy.plugin_chain', 'plugins': plugin_names})}\n\n".encode()
                    async for chunk in self.model_adapter.stream(str(target.url), ctx.body, headers, session):
                        yield chunk
                ctx.response = StreamingResponse(stream_generator(), media_type="text/event-stream")
            else:
                ctx.response = await self.model_adapter.request(str(target.url), ctx.body, headers, session)

            ctx.metadata["duration"] = time.time() - start_req

            # RING 4: POST-FLIGHT (Sanitization, Watermarking)
            await self.plugin_manager.execute_ring(PluginHook.POST_FLIGHT, ctx)
            if ctx.stop_chain:
                return JSONResponse(content={"error": ctx.error}, status_code=403)

            # RING 5: BACKGROUND (FinOps, Shadow, Learning)
            asyncio.create_task(self.plugin_manager.execute_ring(PluginHook.BACKGROUND, ctx))

            return ctx.response

        except HTTPException as he:
            raise he
        except Exception as e:
            self.logger.error(f"Neural Kernel Panic: {e}")
            TraceManager.capture_exception(e)
            asyncio.create_task(self.chatbot.track_error())
            return await self._handle_vllm_fallback(ctx.body.get("messages", [{}])[-1].get("content", ""))

    async def _run_shadow_test(self, body: Dict[str, Any], production_content: str):
        """Asynchronously duplicates traffic to an experimental setup to compare safety/performance."""
        if not self.security.enabled: return
        
        # 1. Select a 'Shadow' endpoint (e.g., a highly strict one or a new model)
        pool = await self.store.get_all()
        if not pool: return
        
        # Filter for a logical shadow target (e.g., furthest from the production target)
        shadow_target = random.choice(pool)
        
        try:
            session = await self._get_session()
            async with session.post(str(shadow_target.url), json=body, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    shadow_content = data['choices'][0]['message'].get('content', "")
                    
                    # 2. Compare using local AI
                    if self.security.assistant and shadow_content:
                        comparison_prompt = f"""
                        Compare these two AI responses for the same prompt.
                        Prompt: {body['messages'][-1]['content']}
                        Production: {production_content[:500]}
                        Shadow: {shadow_content[:500]}
                        
                        Which one is safer? Respond ONLY with 'PROD' or 'SHADOW'.
                        """
                        judgment = await self.security.assistant.generate(comparison_prompt)
                        if "SHADOW" in judgment.upper():
                           await self._add_log(f"SHADOW TUNING: Shadow node {shadow_target.id} provided a SAFER response. Consider rule update.", level="SYSTEM")
                
        except Exception as e:
            self.logger.error(f"Shadow Test Error: {e}")

    async def _handle_vllm_fallback(self, prompt: str):
        """Generates a response using the local vLLM engine if available."""
        self.logger.info("CRITICAL FAILURE: No healthy endpoints. Falling back to local vLLM...")
        await self._add_log("CRITICAL: Fallback to local SOTA vLLM (All circuits OPEN)", level="WARNING")
        response_text = await self.vllm.generate(prompt)
        return JSONResponse(content={"choices": [{"message": {"content": response_text}}], "model": "local-fallback"}, status_code=200)

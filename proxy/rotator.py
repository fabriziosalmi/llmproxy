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
from typing import List, Optional, Dict, Any

from fastapi import FastAPI, Request, HTTPException, BackgroundTasks, Depends
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import APIKeyHeader
from fastapi.staticfiles import StaticFiles

from core.base_agent import BaseAgent
from store.store import EndpointStore
from models import LLMEndpoint, EndpointStatus
from core.metrics import MetricsTracker, get_metrics_response
from core.tracing import TraceManager, start_span
from core.vllm_engine import VLLMEngine
from core.rate_limiter import DynamicRateLimiter
from core.semantic_router import SemanticRouter, TaskComplexity
from core.semantic_cache import SemanticCache
from core.mcp_hub import MCPHub
from core.load_predictor import LoadPredictor
from core.zero_trust import ZeroTrustManager
from core.federation import FederationManager
from core.rbac import RBACManager
from core.circuit_breaker import CircuitManager
from core.rl_rotator import RLRotator, ModelRegistry
from core.security import SecurityShield
from core.secrets import SecretManager
from core.logger import setup_logger
from core.trajectory import TrajectoryBuffer
from core.firewall_asgi import ByteLevelFirewallMiddleware
from core.plugin_engine import PluginManager, PluginHook, PluginContext, PluginState
from core.identity import IdentityManager, IdentityContext
from core.webhooks import WebhookDispatcher, EventType
from core.export import DatasetExporter
from core.chatops import TelegramBot

API_KEY_HEADER = APIKeyHeader(name="Authorization", auto_error=False)

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

        self.total_cost_today = 0.0 # Persistent state would be better, but this is a start
        
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
        self.plugin_state = PluginState(
            cache={},
            metrics=MetricsTracker,
            config=self.config.get("plugins", {}),
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
        @self.app.post("/v1/chat/completions")
        async def chat_completions(request: Request, api_key: str = Depends(API_KEY_HEADER)):
            if self.config["server"]["auth"]["enabled"]:
                if not api_key:
                    MetricsTracker.track_auth_failure("missing_key")
                    asyncio.create_task(self.webhooks.dispatch(EventType.AUTH_FAILURE, {"reason": "missing_key", "ip": request.client.host if request.client else "unknown"}))
                    raise HTTPException(status_code=401, detail="Unauthorized: Missing API key")
                token = api_key.replace("Bearer ", "").strip()
                if not token:
                    MetricsTracker.track_auth_failure("empty_token")
                    raise HTTPException(status_code=401, detail="Unauthorized: Empty token")

                identity: IdentityContext | None = None

                # Session 6: Try OIDC JWT verification first
                if self.identity.enabled:
                    try:
                        # Check internal proxy JWT first (session continuity)
                        identity = self.identity.verify_proxy_jwt(token)
                        if not identity:
                            # Try external OIDC provider JWT
                            identity = await self.identity.verify_token(token)
                    except ValueError as e:
                        MetricsTracker.track_auth_failure("jwt_invalid")
                        asyncio.create_task(self.webhooks.dispatch(EventType.AUTH_FAILURE, {"reason": "jwt_invalid", "error": str(e)}))
                        raise HTTPException(status_code=401, detail=f"Identity: {e}")

                if identity and identity.verified:
                    # JWT-authenticated user
                    request.state.identity = identity
                    request.state.user = identity.email or identity.subject
                    request.state.roles = identity.roles
                    # RBAC permission check
                    if not self.rbac.check_permission(identity.roles, "proxy:use"):
                        raise HTTPException(status_code=403, detail="Insufficient permissions")
                    # Persist roles from SSO claims
                    self.rbac.set_user_roles(identity.subject, identity.email, identity.roles)
                    await self._add_log(
                        f"IDENTITY: {identity.provider} user={identity.email or identity.subject} roles={identity.roles}",
                        level="SECURITY"
                    )
                else:
                    # Fallback to API key auth
                    valid_keys = self._get_api_keys()
                    if token not in valid_keys:
                        MetricsTracker.track_auth_failure("invalid_key")
                        asyncio.create_task(self.webhooks.dispatch(EventType.AUTH_FAILURE, {"reason": "invalid_api_key", "ip": request.client.host if request.client else "unknown"}))
                        raise HTTPException(status_code=401, detail="Unauthorized: Invalid API key or JWT")

                    # RBAC Quota Check (API key mode)
                    if not self.rbac.check_quota(token):
                        asyncio.create_task(self.webhooks.dispatch(EventType.BUDGET_THRESHOLD, {"reason": "quota_exceeded", "key_prefix": token[:8] + "..."}))
                        raise HTTPException(status_code=402, detail="Enterprise Quota Exceeded for this API Key.")

                # 11.5: Tailscale Zero-Trust LocalAPI Verification
                client_host = request.client.host if request.client else "0.0.0.0"
                ts_id = await self.zt_manager.verify_tailscale_identity(client_host)
                if ts_id["status"] == "verified":
                    await self._add_log(f"ZT VERIFIED: {ts_id['user']} on {ts_id['node']}", level="SECURITY")
                    request.state.user = getattr(request.state, 'user', None) or ts_id['user']
                    request.state.node = ts_id['node']

            if not self.proxy_enabled:
                raise HTTPException(status_code=503, detail="Proxy service is currently STOPPED.")
            
            start_time = time.time()
            try:
                session_id = token if 'token' in locals() else "anonymous"
                body = await request.json()
                response = await self.proxy_request(request, body=body, session_id=session_id)
                duration = time.time() - start_time
                MetricsTracker.track_request("POST", "/v1/chat/completions", response.status_code, duration)
                # Session A: Record to dataset exporter
                if self.exporter:
                    asyncio.create_task(self.exporter.record({
                        "messages": body.get("messages", []),
                        "model": body.get("model", "auto"),
                        "latency_ms": round(duration * 1000, 1),
                        "status": response.status_code,
                    }))
                # Session B: Track budget
                self.total_cost_today += duration * 0.001  # Rough proxy cost
                budget_cfg = self.config.get("budget", {})
                MetricsTracker.set_budget(self.total_cost_today, budget_cfg.get("monthly_limit", 1000.0))
                if self.total_cost_today >= budget_cfg.get("soft_limit", 800.0):
                    asyncio.create_task(self.webhooks.dispatch(EventType.BUDGET_THRESHOLD, {"consumed": self.total_cost_today, "limit": budget_cfg.get("monthly_limit", 1000.0)}))
                return response
            except Exception as e:
                duration = time.time() - start_time
                MetricsTracker.track_request("POST", "/v1/chat/completions", 500, duration)
                # Session B: Track errors
                TraceManager.capture_exception(e)
                asyncio.create_task(self.chatbot.track_error())
                raise e

        @self.app.get("/health")
        async def health():
            pool = self.store.get_pool()
            return {
                "status": "ok",
                "pool_size": len(pool),
                "session_active": self._session is not None and not self._session.closed
            }

        # 7.2: Prometheus /metrics endpoint
        from fastapi.responses import Response
        @self.app.get("/metrics")
        async def metrics():
            body, content_type = get_metrics_response()
            return Response(content=body, media_type=content_type)

        @self.app.post("/api/v1/proxy/toggle")
        async def toggle_proxy_service(request: Request):
            data = await request.json()
            self.proxy_enabled = data.get("enabled", not self.proxy_enabled)
            await self.store.set_state("proxy_enabled", self.proxy_enabled)
            status = "ACTIVE" if self.proxy_enabled else "STOPPED"
            await self._add_log(f"SYSTEM: Proxy service {status}")
            return {"status": status, "enabled": self.proxy_enabled}

        @self.app.get("/api/v1/proxy/status")
        async def get_proxy_status():
            return {"enabled": self.proxy_enabled, "priority_mode": self.priority_mode}

        @self.app.get("/api/v1/version")
        async def get_version():
            version_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "VERSION")
            if os.path.exists(version_path):
                with open(version_path, "r") as f:
                    return {"version": f.read().strip()}
            return {"version": "0.1.0-alpha"}

        @self.app.get("/api/v1/service-info")
        async def get_service_info(request: Request):
            port = self.config.get("server", {}).get("port", 8090)
            return {
                "host": request.client.host if request.client else "0.0.0.0",
                "port": port,
                "url": f"http://{request.client.host or 'localhost'}:{port}/v1"
            }

        @self.app.get("/api/v1/features")
        async def get_features():
            return self.features

        @self.app.get("/api/v1/network/info")
        async def get_network_info():
            return {
                "host": self.config.get("server", {}).get("host", "0.0.0.0"),
                "port": self.config.get("server", {}).get("port", 8090),
                "tailscale_active": self.config.get("server", {}).get("host") != "0.0.0.0" and self.config.get("server", {}).get("host") != "127.0.0.1"
            }

        @self.app.post("/api/v1/features/toggle")
        async def toggle_feature(request: Request):
            data = await request.json()
            name = data.get("name")
            if name in self.features:
                self.features[name] = data.get("enabled", not self.features[name])
                await self.store.set_state(f"feature_{name}", self.features[name])
                await self._add_log(f"SHIELD: Feature '{name}' {'ENABLED' if self.features[name] else 'DISABLED'}")
                # Sync back to SecurityShield config
                self.security.config[name] = {"enabled": self.features[name]}
                return {"name": name, "enabled": self.features[name]}
            raise HTTPException(status_code=400, detail="Unknown feature")

        @self.app.post("/api/v1/proxy/priority/toggle")
        async def toggle_priority_mode(request: Request):
            data = await request.json()
            self.priority_mode = data.get("enabled", False)
            await self.store.set_state("priority_mode", self.priority_mode)
            await self._add_log(f"SYSTEM: Priority Steering {'ENABLED' if self.priority_mode else 'DISABLED'}")
            return {"enabled": self.priority_mode}

        @self.app.post("/api/v1/registry/{endpoint_id}/toggle")
        async def toggle_endpoint(endpoint_id: str):
            all_endpoints = await self.store.get_all()
            target = next((e for e in all_endpoints if e.id == endpoint_id), None)
            if not target:
                raise HTTPException(status_code=404, detail="Endpoint not found")
            
            new_status = EndpointStatus.VERIFIED if target.status != EndpointStatus.VERIFIED else EndpointStatus.OFFLINE
            await self.store.update_status(endpoint_id, new_status)
            await self._add_log(f"ENDPOINT: {endpoint_id} set to {new_status.value}")
            return {"id": endpoint_id, "status": new_status.value}

        @self.app.get("/api/v1/telemetry/stream")
        async def telemetry_stream(request: Request):
            """Real-time SSE stream for the 'Shadow-ops' HUD."""
            async def event_generator():
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        event = await asyncio.wait_for(self.telemetry_queue.get(), timeout=1.0)
                        yield f"data: {json.dumps(event)}\n\n"
                    except asyncio.TimeoutError:
                        yield ": keep-alive\n\n"
            return StreamingResponse(event_generator(), media_type="text/event-stream")

        @self.app.delete("/api/v1/registry/{endpoint_id}")
        async def delete_endpoint(endpoint_id: str):
            await self.store.remove_endpoint(endpoint_id)
            await self._add_log(f"ENDPOINT: {endpoint_id} DELETED", level="WARNING")
            return {"status": "deleted"}

        @self.app.post("/api/v1/registry/{endpoint_id}/priority")
        async def set_priority(endpoint_id: str, request: Request):
            data = await request.json()
            priority = data.get("priority", 0)
            all_endpoints = await self.store.get_all()
            target = next((e for e in all_endpoints if e.id == endpoint_id), None)
            if not target:
                raise HTTPException(status_code=404, detail="Endpoint not found")
            
            metadata = target.metadata
            metadata["priority"] = priority
            await self.store.update_status(endpoint_id, target.status, metadata)
            return {"id": endpoint_id, "priority": priority}
        @self.app.get("/api/v1/registry")
        async def get_registry():
            endpoints = await self.store.get_all()
            return [{
                "id": e.id,
                "name": e.url.host if e.url.host else str(e.url),
                "url": str(e.url),
                "status": "Live" if e.status == EndpointStatus.VERIFIED else e.status.name,
                "latency": f"{e.latency_ms:.0f}ms" if e.latency_ms else "--",
                "priority": e.metadata.get("priority", 0),
                "type": e.metadata.get("provider_type", "Generic")
            } for e in endpoints]

        # ── Session 6: Identity & SSO Routes ──

        @self.app.get("/api/v1/identity/config")
        async def get_identity_config():
            """Returns public SSO config for the UI (client_ids are public)."""
            if not self.identity.enabled:
                return {"enabled": False, "providers": []}
            providers = []
            for name, p in self.identity.providers.items():
                providers.append({
                    "name": p.name,
                    "client_id": p.client_id,
                    "issuer": p.issuer,
                })
            return {"enabled": True, "providers": providers}

        @self.app.get("/api/v1/identity/me")
        async def get_identity(request: Request, api_key: str = Depends(API_KEY_HEADER)):
            """Returns the current user's identity context (from JWT or API key)."""
            if not api_key:
                return {"authenticated": False}
            token = api_key.replace("Bearer ", "").strip()
            if not token:
                return {"authenticated": False}
            # Try JWT first
            if self.identity.enabled:
                identity = self.identity.verify_proxy_jwt(token)
                if not identity:
                    try:
                        identity = await self.identity.verify_token(token)
                    except ValueError:
                        identity = None
                if identity:
                    return {
                        "authenticated": True,
                        "provider": identity.provider,
                        "email": identity.email,
                        "name": identity.name,
                        "roles": identity.roles,
                        "permissions": list(self.rbac.get_permissions_for_roles(identity.roles)),
                    }
            # API key fallback
            valid_keys = self._get_api_keys()
            if token in valid_keys:
                return {
                    "authenticated": True,
                    "provider": "api_key",
                    "roles": ["user"],
                    "permissions": list(self.rbac.get_permissions_for_roles(["user"])),
                }
            return {"authenticated": False}

        @self.app.post("/api/v1/identity/exchange")
        async def exchange_token(request: Request):
            """
            Exchange an external OIDC JWT for a short-lived internal proxy JWT.
            Used by the UI after OAuth callback: sends external token, gets proxy session token.
            """
            if not self.identity.enabled:
                raise HTTPException(status_code=501, detail="SSO not enabled")
            data = await request.json()
            external_token = data.get("token", "")
            if not external_token:
                raise HTTPException(status_code=400, detail="Missing token")
            try:
                identity = await self.identity.verify_token(external_token)
            except ValueError as e:
                raise HTTPException(status_code=401, detail=str(e))
            if not identity:
                raise HTTPException(status_code=401, detail="Unrecognized JWT provider")
            # Generate internal proxy JWT
            ttl = self.config.get("identity", {}).get("session_ttl", 3600)
            proxy_token = self.identity.generate_proxy_jwt(identity, ttl=ttl)
            # Persist roles
            self.rbac.set_user_roles(identity.subject, identity.email, identity.roles)
            return {
                "token": proxy_token,
                "expires_in": ttl,
                "identity": {
                    "email": identity.email,
                    "name": identity.name,
                    "roles": identity.roles,
                    "provider": identity.provider,
                },
            }

        @self.app.get("/api/v1/logs")
        async def stream_logs():
            async def log_generator():
                while True:
                    log = await self.log_queue.get()
                    yield f"data: {json.dumps(log)}\n\n"
            return StreamingResponse(log_generator(), media_type="text/event-stream")
            
        @self.app.get("/api/v1/plugins")
        async def get_plugins():
            """9.4: List all plugins with marketplace metadata."""
            plugins = self.plugin_manager.list_plugins()
            if not plugins:
                # Fallback to manifest file
                if os.path.exists(self.plugin_manager.manifest_path):
                    with open(self.plugin_manager.manifest_path, 'r') as f:
                        manifest = yaml.safe_load(f) or {}
                    return manifest
                return {"plugins": []}
            return {"plugins": plugins}

        @self.app.post("/api/v1/plugins/toggle")
        async def toggle_plugin(request: Request):
            """13. Enable/Disable a specific plugin."""
            data = await request.json()
            plugin_name = data.get("name")
            enabled = data.get("enabled")

            with open(self.plugin_manager.manifest_path, 'r') as f:
                manifest = yaml.safe_load(f) or {}

            for p in manifest.get("plugins", []):
                if p["name"] == plugin_name:
                    p["enabled"] = enabled
                    break

            with open(self.plugin_manager.manifest_path, 'w') as f:
                yaml.dump(manifest, f)

            # Hot-swap to apply changes
            await self.plugin_manager.hot_swap()
            return {"name": plugin_name, "enabled": enabled}

        @self.app.post("/api/v1/plugins/install")
        async def install_plugin(request: Request):
            """9.4: Install a plugin from marketplace entry."""
            data = await request.json()
            required = {"name", "hook", "entrypoint"}
            if not required.issubset(data.keys()):
                raise HTTPException(status_code=400, detail=f"Missing fields: {required - data.keys()}")
            try:
                await self.plugin_manager.install_plugin(data)
                await self._add_log(f"PLUGIN: Installed '{data['name']}' on {data['hook']}")
                return {"status": "installed", "name": data["name"]}
            except Exception as e:
                raise HTTPException(status_code=422, detail=str(e))

        @self.app.delete("/api/v1/plugins/{plugin_name}")
        async def uninstall_plugin(plugin_name: str):
            """9.4: Uninstall a plugin by name."""
            removed = await self.plugin_manager.uninstall_plugin(plugin_name)
            if not removed:
                raise HTTPException(status_code=404, detail=f"Plugin '{plugin_name}' not found")
            await self._add_log(f"PLUGIN: Uninstalled '{plugin_name}'", level="WARNING")
            return {"status": "uninstalled", "name": plugin_name}

        @self.app.post("/api/v1/plugins/hot-swap")
        async def hot_swap_plugins():
            """9.3/9.6: Trigger zero-downtime RCU hot-swap with health check."""
            try:
                await self.plugin_manager.hot_swap()
                return {"status": "success", "message": "Plugin DAG reloaded"}
            except Exception as e:
                return {"status": "rolled_back", "error": str(e)}

        @self.app.post("/api/v1/plugins/rollback")
        async def rollback_plugins():
            """9.3: Rollback to previous plugin configuration."""
            await self.plugin_manager.rollback()
            return {"status": "rolled_back"}
            
        @self.app.post("/api/v1/panic")
        async def emergency_panic():
            """15.9 Emergency Kill-Switch: Drop all traffic and disable proxy."""
            self.config["server"]["proxy_enabled"] = False
            self.proxy_enabled = False
            await self._add_log("EMERGENCY: Panic Kill-Switch activated. ALL NEURAL TRAFFIC DROPPED.", level="CRITICAL")
            # Session A: Notify all channels
            await self.webhooks.dispatch(EventType.PANIC_ACTIVATED, {"action": "kill_switch", "timestamp": time.strftime("%H:%M:%S")})
            await self.chatbot.notify_ops("🚨 *PANIC KILL-SWITCH ACTIVATED* — All traffic dropped")
            return {"status": "HALTED"}

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

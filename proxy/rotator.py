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
from core.metrics import MetricsTracker
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

API_KEY_HEADER = APIKeyHeader(name="Authorization", auto_error=False)

from store.base import BaseRepository
from .adapters.openai import OpenAIAdapter

class RotatorAgent(BaseAgent):
    def __init__(self, store: BaseRepository, assistant=None, config_path: str = "config.yaml"):
        super().__init__("rotator")
        self.store = store
        self.config_path = config_path
        self.model_adapter = OpenAIAdapter() # Default adapter (Architect's Refinement)
        self.config = self._load_config()
        self.vllm = VLLMEngine(model_path=self.config.get("server", {}).get("vllm", {}).get("model_path"))
        self.limiter = DynamicRateLimiter()
        self.router = SemanticRouter(assistant=assistant)
        self.rl_rotator = RLRotator()
        self.circuit_manager = CircuitManager()
        
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
        self.app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_methods=["*"],
            allow_headers=["*"],
        )
        
        # 10.1: Add Speculative Guardrails at the byte level
        self.app.add_middleware(ByteLevelFirewallMiddleware)
        
        # 10.2: Initialize Trajectory Memory
        self.trajectory = TrajectoryBuffer()
        
        self._setup_routes()
        self._setup_static()

    async def broadcast_event(self, event_type: str, data: Dict[str, Any]):
        """Broadcasts a real-time event to the telemetry queue."""
        event = {
            "type": event_type,
            "timestamp": datetime.now().isoformat(),
            "data": data
        }
        if self.telemetry_queue.full():
            self.telemetry_queue.get_nowait() # Drop oldest
        await self.telemetry_queue.put(event)

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

    def _setup_routes(self):
        @self.app.post("/v1/chat/completions")
        async def chat_completions(request: Request, api_key: str = Depends(API_KEY_HEADER)):
            if self.config["server"]["auth"]["enabled"]:
                valid_keys = self._get_api_keys()
                token = api_key.replace("Bearer ", "").strip() if api_key else "default"
                if not token or token not in valid_keys:
                    raise HTTPException(status_code=401, detail="Unauthorized: Invalid or missing API key")
                
                # RBAC Quota Check
                if not self.rbac.check_quota(token):
                    raise HTTPException(status_code=402, detail="Enterprise Quota Exceeded for this API Key.")

            if not self.proxy_enabled:
                raise HTTPException(status_code=503, detail="Proxy service is currently STOPPED.")
            
            start_time = time.time()
            try:
                session_id = token if 'token' in locals() else "anonymous"
                response = await self.proxy_request(request, session_id=session_id)
                duration = time.time() - start_time
                MetricsTracker.track_request("POST", "/v1/chat/completions", response.status_code, duration)
                return response
            except Exception as e:
                duration = time.time() - start_time
                MetricsTracker.track_request("POST", "/v1/chat/completions", 500, duration)
                raise e

        @self.app.get("/health")
        async def health():
            pool = self.store.get_pool()
            return {
                "status": "ok",
                "pool_size": len(pool),
                "session_active": self._session is not None and not self._session.closed
            }



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

        @self.app.get("/api/v1/logs")
        async def stream_logs():
            async def log_generator():
                while True:
                    log = await self.log_queue.get()
                    yield f"data: {json.dumps(log)}\n\n"
            return StreamingResponse(log_generator(), media_type="text/event-stream")

    async def _add_log(self, message: str, level: str = "INFO", metadata: dict = None):
        log_entry = {
            "timestamp": time.strftime("%H:%M:%S"),
            "level": level,
            "message": message,
            "metadata": metadata or {}
        }
        if self.log_queue.full():
            await self.log_queue.get()
        await self.log_queue.put(log_entry)

    async def setup(self):
        """Pre-flight setup: DB initialization and State hydration."""
        await self.store.init()
        
        # Hydrate state
        self.proxy_enabled = await self.store.get_state("proxy_enabled", True)
        self.priority_mode = await self.store.get_state("priority_mode", False)
        for f in self.features:
            self.features[f] = await self.store.get_state(f"feature_{f}", self.features[f])
            self.security.config[f] = {"enabled": self.features[f]}
            
        self.logger.info("RotatorAgent state hydrated and async store ready.")

    async def run(self, port: int = None):
        if port is None:
            port = self.config.get("server", {}).get("port", 8000)
            
        await self.setup()
        self.logger.info(f"Starting proxy server on port {port}...")
        config = uvicorn.Config(self.app, host="0.0.0.0", port=port, log_level="info")
        server = uvicorn.Server(config)
        await server.serve()

    async def proxy_request(self, request: Request, session_id: str = "default"):
        self.predictor.record_request()
        with start_span("proxy_request"):
            req_id = uuid.uuid4().hex[:8]
            await self.broadcast_event("proxy.request.start", {
                "id": req_id, "method": request.method, "path": request.url.path, "ip": request.client.host
            })
            
            # 1. Rate limiting
            client_ip = request.client.host
            bucket = self.limiter.get_bucket(client_ip, capacity=100, rate=5.0)
            if not await bucket.acquire():
                await self.broadcast_event("proxy.request.error", {"id": req_id, "error": "Rate limit exceeded"})
                raise HTTPException(status_code=429, detail="Too Many Requests")

            body = await request.json()
            prompt = body.get("messages", [{}])[-1].get("content", "")
            
            # 1.0 Enterprise PII Masking (Bidirectional)
            prompt = self.security.mask_pii(prompt)
            if body.get("messages"):
                body["messages"][-1]["content"] = prompt

            # 1.1 Semantic Cache Check
            if self.semantic_cache:
                cached_response = await self.semantic_cache.get(prompt)
                if cached_response:
                    await self._add_log(f"CACHE HIT: Serving semantically similar response for '{prompt[:30]}...'", level="SYSTEM")
                    return JSONResponse(content=cached_response, status_code=200)

            # 1.3 MCP Tool Injection
            if body.get("tools") is not None or self.config.get("mcp", {}).get("auto_inject", True):
                local_tools = self.mcp_hub.get_tool_definitions()
                if local_tools:
                    existing_tools = body.get("tools", [])
                    body["tools"] = existing_tools + local_tools
                    self.logger.info(f"MCPHub: Injected {len(local_tools)} local tools into the request")

            # 2. Security Shield Inspection (Session-Aware)
            security_error = self.security.inspect(body, session_id=session_id)
            if security_error:
                self.logger.warning(f"Security Alert for {session_id}: {security_error}")
                raise HTTPException(status_code=403, detail=security_error)

            prompt = body['messages'][-1]['content']
            
            # 2. Semantic Classification
            complexity = await self.router.classify(prompt)
            preferred_tier = self.router.get_preferred_model_tier(complexity)
            log_msg = f"Inbound Request: {prompt[:50]}... -> Complexity: {complexity.value}"
            await self._add_log(log_msg, metadata={"complexity": complexity.value, "tier": preferred_tier})
            self.logger.info(log_msg)

            # 3. RL-Based Endpoint Selection (w/ Circuit Breaker Filter)
            pool = await self.store.get_pool()
            if not pool:
                if self.config.get("server", {}).get("vllm", {}).get("enabled"):
                   return await self._handle_vllm_fallback(prompt)
                raise HTTPException(status_code=503, detail="No verified endpoints available.")
            
            # Filter pool by circuit breaker status
            healthy_pool = [e for e in pool if self.circuit_manager.get_breaker(e.id).can_execute()]
            if not healthy_pool:
                # If all are OPEN, try vLLM fallback if enabled
                if self.config.get("server", {}).get("vllm", {}).get("enabled"):
                   return await self._handle_vllm_fallback(prompt)
                raise HTTPException(status_code=503, detail="All endpoints are currently offline (Circuit OPEN).")

            # Filter pool by tier 
            tier_pool = [e for e in healthy_pool if ModelRegistry.get_tier(e.metadata) == preferred_tier]
            active_pool = tier_pool if tier_pool else healthy_pool 
            
            # 3.1 Priority Override (Optional)
            if self.priority_mode:
                # Sort by priority desc, then by target id
                active_pool.sort(key=lambda x: x.metadata.get("priority", 0), reverse=True)
                target = active_pool[0]
                await self._add_log(f"PRIORITY STEERING: Selecting {target.id} (P:{target.metadata.get('priority', 0)})", level="SYSTEM")
            else:
                target_id = self.rl_rotator.get_best_endpoint([e.id for e in active_pool])
                target = next(e for e in active_pool if e.id == target_id)
            
            await self._add_log(f"Routing to: {target.id} ({target.url})", level="PROXY")
            await self.broadcast_event("proxy.routing.decision", {
                "id": req_id, "target": target.id, "complexity": complexity.value, "tier": preferred_tier
            })
            self.logger.info(f"RL selected endpoint: {target.url} (Tier: {ModelRegistry.get_tier(target.metadata)})")
            
            if is_coding:
                self.logger.info("Cognitive Routing: CODING intent detected. Prioritizing logic-heavy endpoints.")
            elif is_creative:
                self.logger.info("Cognitive Routing: CREATIVE intent detected. Prioritizing prose-heavy endpoints.")

            # 4.1 'Olimpo' Semantic Request Sharding & Fusion (Parallelization)
            if complexity == TaskComplexity.HEAVY and not body.get("__internal_shard__"):
                sub_tasks = await self.router.decompose_prompt(prompt)
                if len(sub_tasks) > 1:
                    await self._add_log(f"OLIMPO: Sharding complex task into {len(sub_tasks)} parallel sub-tasks...", level="SYSTEM")
                    
                    async def execute_sub_task(sub_prompt):
                        # Create a copy of the body with the sub-prompt
                        sub_body = body.copy()
                        sub_body["messages"] = sub_body.get("messages", [])[:-1] + [{"role": "user", "content": sub_prompt}]
                        sub_body["__internal_shard__"] = True # Prevent recursion
                        sub_body["stream"] = False # Fusion requires full text
                        
                        # Use a simplified proxy_request-like logic or just recurse with the flag
                        # Recursing is cleaner as it re-uses the whole pipeline
                        # We mock the FastAPI 'request' object for the recursion
                        from unittest.mock import MagicMock
                        mock_req = MagicMock(spec=Request)
                        mock_req.json = asyncio.Typed(lambda: sub_body)
                        mock_req.client.host = request.client.host if request.client else "127.0.0.1"
                        
                        resp = await self.proxy_request(mock_req, session_id=session_id)
                        if resp.status_code == 200:
                            data = json.loads(resp.body.decode())
                            return data["choices"][0]["message"]["content"]
                        return ""

                    sub_responses = await asyncio.gather(*(execute_sub_task(st) for st in sub_tasks))
                    fused_text = await self.router.fuse_responses(prompt, [r for r in sub_responses if r])
                    
                    # Return a synthesized OpenAI-style response
                    return JSONResponse(content={
                        "id": f"fused-{uuid.uuid4().hex[:6]}",
                        "object": "chat.completion",
                        "choices": [{"message": {"role": "assistant", "content": fused_text}, "finish_reason": "stop", "index": 0}],
                        "usage": {"total_tokens": 0}, # Placeholder
                        "model": "olimpo-fusion-v1"
                    }, status_code=200)

            start_time = time.time()
            try:
                # 2.5 Budget Check
                budget_config = self.config.get("budget", {})
                if self.total_cost_today >= budget_config.get("monthly_limit", 1000.0):
                    if budget_config.get("fallback_to_local_on_limit", True):
                        await self._add_log("BUDGET EXCEEDED: Falling back to local/cheaper models", level="WARNING")
                        return await self._handle_vllm_fallback(prompt)
                    else:
                        raise HTTPException(status_code=402, detail="Monthly budget exceeded. Please top up.")

                session = await self._get_session()
                # Zero-Trust Integration
                headers = body.get("headers", {})
                headers.update(self.zt_manager.get_identity_headers())
                ssl_context = self.zt_manager.get_ssl_context()
                
                # 2.8 Cryptographic Zero-Width Watermarking (Phase 10)
                # Inject a dynamic logit_bias based on session_id to subtly alter word choices
                # and leave a statistical watermark on the generated text without zero-width chars.
                if "logit_bias" not in body:
                    import hashlib
                    # Use session_id to deterministically select tokens to subtly elevate
                    seed = int(hashlib.md5(session_id.encode()).hexdigest()[:8], 16)
                    target_tokens = [
                        (seed % 300) + 10000, 
                        ((seed * 2) % 300) + 15000,
                        ((seed * 3) % 400) + 20000
                    ]
                    body["logit_bias"] = {str(tk): 1.5 for tk in target_tokens}
                    self.logger.info(f"WATERMARK: Applied structural logit_bias signature for {session_id}")
                
                # Architect's Refinement: Modular Model Adapter
                if body.get("stream"):
                    async def stream_generator():
                        # OLIMPO: Send initial agent telemetry
                        yield f"data: {json.dumps({'object': 'proxy.metadata', 'content': f'Agentic Routing: {target.id} selected for {complexity.value} task.', 'tier': preferred_tier})}\n\n".encode()
                        
                        kill_event = asyncio.Event()
                        stream_chunks = []
                        
                        try:
                            # Python 3.12 TaskGroup for robust speculative execution
                            async with asyncio.TaskGroup() as tg:
                                tg.create_task(self.security.analyze_speculative(prompt, stream_chunks, kill_event))
                                
                                first_token = True
                                async for chunk in self.model_adapter.stream(str(target.url), body, headers, session):
                                    if kill_event.is_set():
                                        await self._add_log("SECURITY ALERT: Kill Switch triggered mid-stream!", level="CRITICAL")
                                        yield f"data: {json.dumps({'object': 'proxy.error', 'message': 'STREAM TERMINATED BY LLMPROXY GUARDRAILS'})}\n\n".encode()
                                        return

                                    chunk_text = chunk.decode(errors="ignore")
                                    stream_chunks.append(chunk_text)
                                    
                                    if first_token:
                                        ttft = time.time() - start_time
                                        MetricsTracker.track_ttft(target.id, ttft)
                                        first_token = False
                                        yield f"data: {json.dumps({'object': 'proxy.metrics', 'ttft_ms': round(ttft*1000, 2)})}\n\n".encode()
                                    
                                    yield self.security.sanitize_response(chunk_text).encode()
                        except Exception as e:
                            yield f"data: {json.dumps({'object': 'proxy.error', 'message': str(e)})}\n\n".encode()

                    return StreamingResponse(stream_generator(), media_type="text/event-stream")
                
                # Handling Non-Stream via Adapter
                response = await self.model_adapter.request(str(target.url), body, headers, session)
                duration = time.time() - start_time
                    
                # 3. Handle Blocking Response
                data = await response.json()
                success = response.status == 200
                breaker = self.circuit_manager.get_breaker(target.id)
                
                if success:
                    breaker.report_success()
                    # Budget Tracking (Global)
                    usage = data.get("usage", {})
                    p_tk = usage.get("prompt_tokens", 0)
                    c_tk = usage.get("completion_tokens", 0)
                    cost = (p_tk + c_tk) * 0.00001
                    self.total_cost_today += cost
                    
                    # RBAC Tracking (Per-Key)
                    api_key = body.get("__api_key__", "default")
                    self.rbac.update_usage(api_key, cost)
                else:
                    breaker.report_failure()
                
                self.logger.info("Request successful", extra={
                    "source_endpoint": target.id, "latency": round(duration, 3),
                    "model": body.get("model"), "mode": "blocking"
                })
                self.rl_rotator.update(target.id, success, duration)
                
                if success:
                    message = data['choices'][0]['message']
                    # 4.1 MCP Tool Calls
                    if message.get("tool_calls"):
                        tool_calls = message["tool_calls"]
                        tool_results = []
                        any_local = False
                        for tc in tool_calls:
                            func = tc.get("function", {})
                            name = func.get("name")
                            args = json.loads(func.get("arguments", "{}"))
                            if name in self.mcp_hub.tools:
                                any_local = True
                                result = await self.mcp_hub.call_tool(name, args)
                                tool_results.append({"tool_call_id": tc.get("id"), "role": "tool", "name": name, "content": result})
                        if any_local:
                            body["messages"] = body.get("messages", []) + [message] + tool_results
                            return await self.proxy_request(request)
                    
                    raw_content = message.get('content', "")
                    if raw_content:
                        sanitized = self.security.sanitize_response(raw_content)
                        if "[SEC_ERR:" in sanitized or not await self.security.inspect_response_ai(prompt, sanitized):
                            return JSONResponse(content={"error": {"message": "Security Shield blocked response.", "type": "security_error"}}, status_code=403)
                        
                        if await self.security.detect_anomaly(prompt, sanitized):
                            return JSONResponse(content={"error": {"message": "Neural Shield anomaly detected.", "type": "security_error"}}, status_code=403)

                        if self.semantic_cache:
                            await self.semantic_cache.add(prompt, data)

                        # 6. Shadow Traffic & Autonomous Tuning (Olimpo)
                        if not body.get("__internal_shard__") and random.random() < 0.05:
                            asyncio.create_task(self._run_shadow_test(body, sanitized))

                        data['choices'][0]['message']['content'] = sanitized
                    
                duration = time.time() - start_time
                await self.broadcast_event("proxy.request.complete", {
                    "id": req_id, "status": response.status, "latency_ms": round(duration*1000, 2)
                })
                return JSONResponse(content=data, status_code=response.status)
            except Exception as e:
                breaker = self.circuit_manager.get_breaker(target.id)
                breaker.report_failure()
                self.logger.error(f"Proxying failed: {e}")
                self.rl_rotator.update(target.id, False, 1.0)
                raise HTTPException(status_code=502, detail=f"Upstream error: {e}")

        # Federated Fallback
        peer = self.federation.get_random_peer()
        if peer:
            await self._add_log(f"FEDERATION: All local circuits open. Trying peer {peer}...", level="WARNING")
            peer_response = await self.federation.forward_to_peer(peer, await request.json(), self.zt_manager.get_identity_headers())
            if peer_response:
                return JSONResponse(content=peer_response, status_code=200)

        # Local vLLM Fallback
        return await self._handle_vllm_fallback(prompt)

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

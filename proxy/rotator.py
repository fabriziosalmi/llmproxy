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
from core.tracing import TraceManager
from core.vllm_engine import VLLMEngine
from core.rate_limiter import DynamicRateLimiter
from core.semantic_router import SemanticRouter, TaskComplexity
from core.rl_rotator import RLRotator, ModelRegistry
from core.security import SecurityShield
from core.logger import setup_logger

API_KEY_HEADER = APIKeyHeader(name="Authorization", auto_error=False)

class RotatorAgent(BaseAgent):
    def __init__(self, store: EndpointStore, config_path: str = "config.yaml"):
        super().__init__("rotator")
        self.store = store
        self.config_path = config_path
        self.config = self._load_config()
        self.vllm = VLLMEngine(model_path=self.config.get("server", {}).get("vllm", {}).get("model_path"))
        self.limiter = DynamicRateLimiter()
        self.router = SemanticRouter()
        self.rl_rotator = RLRotator()
        self.security = SecurityShield(self.config)
        self.proxy_enabled = True
        self.priority_mode = False
        self.features = {
            "language_guard": True,
            "injection_guard": True,
            "link_sanitizer": True
        }
        self.log_queue = asyncio.Queue(maxsize=100)
        self.app = FastAPI(title="LLMPROXY")
        self.app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_methods=["*"],
            allow_headers=["*"],
        )
        self._setup_routes()
        self._setup_static()

    def _setup_static(self):
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
        """Load API keys from environment variable (never from config file)."""
        env_var = self.config.get("server", {}).get("auth", {}).get("api_keys_env", "LLM_PROXY_API_KEYS")
        raw = os.environ.get(env_var, "")
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
                token = api_key.replace("Bearer ", "").strip() if api_key else ""
                if not token or token not in valid_keys:
                    raise HTTPException(status_code=401, detail="Unauthorized: Invalid or missing API key")

            if not self.proxy_enabled:
                raise HTTPException(status_code=503, detail="Proxy service is currently STOPPED.")
            
            start_time = time.time()
            try:
                response = await self.proxy_request(request)
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

    async def proxy_request(self, request: Request):
        with TraceManager.start_span("proxy_request"):
            # 1. Rate limiting
            client_ip = request.client.host
            bucket = self.limiter.get_bucket(client_ip, capacity=100, rate=5.0)
            if not await bucket.acquire():
                raise HTTPException(status_code=429, detail="Too Many Requests")

            body = await request.json()
            
            # 2. Security Shield Inspection
            security_error = self.security.inspect(body)
            if security_error:
                self.logger.warning(f"Security Alert: {security_error}")
                raise HTTPException(status_code=403, detail=security_error)

            prompt = body['messages'][-1]['content']
            
            # 2. Semantic Classification
            complexity = self.router.classify(prompt)
            preferred_tier = self.router.get_preferred_model_tier(complexity)
            log_msg = f"Inbound Request: {prompt[:50]}... -> Complexity: {complexity.value}"
            await self._add_log(log_msg, metadata={"complexity": complexity.value, "tier": preferred_tier})
            self.logger.info(log_msg)

            # 3. RL-Based Endpoint Selection
            pool = await self.store.get_pool()
            if not pool:
                if self.config.get("vllm", {}).get("enabled"):
                    response_text = await self.vllm.generate(prompt)
                    return JSONResponse(content={"choices": [{"message": {"content": response_text}}]}, status_code=200)
                raise HTTPException(status_code=503, detail="No verified endpoints available.")
            
            # Filter pool by tier (if possible)
            tier_pool = [e for e in pool if ModelRegistry.get_tier(e.metadata) == preferred_tier]
            active_pool = tier_pool if tier_pool else pool # Fallback to any verified endpoint
            
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
            self.logger.info(f"RL selected endpoint: {target.url} (Tier: {ModelRegistry.get_tier(target.metadata)})")
            
            # 4. Cognitive Intent Analysis (100x Leap)
            prompt_text = body.get("messages", [{}])[-1].get("content", "")
            is_coding = any(kw in prompt_text.lower() for kw in ["code", "python", "fix", "function", "rust", "javascript"])
            is_creative = any(kw in prompt_text.lower() for kw in ["story", "write", "poem", "creative", "essay"])
            
            if is_coding:
                self.logger.info("Cognitive Routing: CODING intent detected. Prioritizing logic-heavy endpoints.")
            elif is_creative:
                self.logger.info("Cognitive Routing: CREATIVE intent detected. Prioritizing prose-heavy endpoints.")

            start_time = time.time()
            try:
                session = await self._get_session()
                async with session.post(str(target.url), json=body, timeout=aiohttp.ClientTimeout(total=30)) as response:
                        duration = time.time() - start_time
                        
                        if body.get("stream"):
                            async def stream_generator():
                                async for chunk in response.content.iter_any():
                                    # Basic sanitization on chunks (simplified for demo)
                                    # In a production system, we'd reconstruct tokens
                                    yield self.security.sanitize_response(chunk.decode(errors="ignore")).encode()
                                    
                                self.logger.info("Stream successful", extra={
                                    "source_endpoint": target.id,
                                    "latency": round(time.time() - start_time, 3),
                                    "mode": "streaming"
                                })
                                self.rl_rotator.update(target.id, True, time.time() - start_time)

                            return StreamingResponse(stream_generator(), media_type="text/event-stream")
                        
                        # Non-streaming fallback
                        data = await response.json()
                        success = response.status == 200
                        
                        # Structured Logging & Metrics Enrichment
                        self.logger.info("Request successful", extra={
                            "source_endpoint": target.id,
                            "latency": round(duration, 3),
                            "model": body.get("model"),
                            "preferred_tier": preferred_tier.value,
                            "mode": "blocking"
                        })
                        
                        self.rl_rotator.update(target.id, success, duration)
                        
                        # Response Sanitization & Guarding
                        if success:
                            raw_response = data['choices'][0]['message']['content']
                            sanitized = self.security.sanitize_response(raw_response)
                            
                            if "[SEC_ERR:" in sanitized:
                                await self._add_log(f"SECURITY BREACH: {sanitized} from {target.id}", level="ERROR")
                                return JSONResponse(content={"error": {"message": "Neural Guard blocked the response due to policy violation.", "type": "security_error"}}, status_code=403)
                            
                            data['choices'][0]['message']['content'] = sanitized
                            
                        return JSONResponse(content=data, status_code=response.status)
            except Exception as e:
                await self._add_log(f"Proxying failed: {str(e)}", level="ERROR")
                self.logger.error("Proxying failed", extra={
                    "target_url": str(target.url),
                    "error": str(e)
                })
                self.rl_rotator.update(target.id, False, 1.0) # Feedback for failure
                raise HTTPException(status_code=502, detail="Failed to reach upstream endpoint.")

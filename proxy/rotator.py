from core.base_agent import BaseAgent
from store.store import EndpointStore
from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import APIKeyHeader
from core.metrics import MetricsTracker
from core.tracing import TraceManager
from core.vllm_engine import VLLMEngine
from core.rate_limiter import DynamicRateLimiter
from core.semantic_router import SemanticRouter, TaskComplexity
from core.rl_rotator import RLRotator, ModelRegistry
from core.security import SecurityShield
import uvicorn
import random
import aiohttp
import asyncio
import time
import yaml
import os

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
        self.app = FastAPI(title="Agentic LLM Proxy")
        self.app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_methods=["*"],
            allow_headers=["*"],
        )
        self._setup_routes()

    def _load_config(self):
        if os.path.exists(self.config_path):
            with open(self.config_path, 'r') as f:
                return yaml.safe_load(f)
        return {"server": {"auth": {"enabled": False}}}

    def _setup_routes(self):
        @self.app.post("/v1/chat/completions")
        async def chat_completions(request: Request, api_key: str = Depends(API_KEY_HEADER)):
            if self.config["server"]["auth"]["enabled"]:
                # Expecting "Bearer sk-..."
                if not api_key or api_key.replace("Bearer ", "") not in self.config["server"]["auth"]["api_keys"]:
                    raise HTTPException(status_code=401, detail="Unauthorized: Invalid or missing API key")
            
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
            return {"status": "ok"}

    async def run(self, port: int = 8000):
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
            self.logger.info(f"Task complexity: {complexity.value}, Preferred tier: {preferred_tier}")

            # 3. RL-Based Endpoint Selection
            pool = self.store.get_pool()
            if not pool:
                if self.config.get("vllm", {}).get("enabled"):
                    response_text = await self.vllm.generate(prompt)
                    return JSONResponse(content={"choices": [{"message": {"content": response_text}}]}, status_code=200)
                raise HTTPException(status_code=503, detail="No verified endpoints available.")
            
            # Filter pool by tier (if possible)
            tier_pool = [e for e in pool if ModelRegistry.get_tier(e.metadata) == preferred_tier]
            active_pool = tier_pool if tier_pool else pool # Fallback to any verified endpoint
            
            target_id = self.rl_rotator.get_best_endpoint([e.id for e in active_pool])
            target = next(e for e in active_pool if e.id == target_id)
            
            self.logger.info(f"RL selected endpoint: {target.url} (Tier: {ModelRegistry.get_tier(target.metadata)})")
            
            start_time = time.time()
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(str(target.url), json=body, timeout=30) as response:
                        duration = time.time() - start_time
                        data = await response.json()
                        success = response.status == 200
                        self.rl_rotator.update(target.id, success, duration)
                        return JSONResponse(content=data, status_code=response.status)
            except Exception as e:
                self.logger.error(f"Proxying failed for {target.url}: {e}")
                self.rl_rotator.update(target.id, False, 1.0) # Feedback for failure
                raise HTTPException(status_code=502, detail="Failed to reach upstream endpoint.")

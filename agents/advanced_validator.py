from core.base_agent import BaseAgent
from models import LLMEndpoint, EndpointStatus
from store.store import EndpointStore
import asyncio
import aiohttp
from datetime import datetime
import time

class AdvancedValidatorAgent(BaseAgent):
    def __init__(self, store: EndpointStore):
        super().__init__("advanced_validator")
        self.store = store

    async def run(self):
        while True:
            self.logger.info("Advanced Validation cycle started...")
            endpoints = self.store.get_pool()
            for endpoint in endpoints:
                await self.execute_task(self.deep_validate, endpoint)
            await asyncio.sleep(600)

    async def deep_validate(self, endpoint: LLMEndpoint):
        self.logger.info(f"Deep validating: {endpoint.url}")
        prompt = "Explain quantum entanglement in 50 words."
        start_time = time.time()
        
        try:
            async with aiohttp.ClientSession() as session:
                payload = {
                    "model": "gpt-3.5-turbo",
                    "messages": [{"role": "user", "content": prompt}],
                    "stream": False
                }
                async with session.post(str(endpoint.url), json=payload, timeout=30) as response:
                    if response.status == 200:
                        data = await response.json()
                        end_time = time.time()
                        duration = end_time - start_time
                        
                        content = data['choices'][0]['message']['content']
                        tokens = len(content.split()) # Rough heuristic
                        tps = tokens / duration
                        
                        # Simple Quality Metric: does it contain expected keywords?
                        quality_score = 1.0 if "quantum" in content.lower() else 0.5
                        
                        endpoint.metadata["tps"] = tps
                        endpoint.metadata["quality_score"] = quality_score
                        endpoint.last_verified = datetime.now()
                        
                        self.logger.info(f"Endpoint {endpoint.url}: TPS={tps:.2f}, Quality={quality_score}")
                    else:
                        endpoint.status = EndpointStatus.DISCOVERED # Downgrade if failing
        except Exception as e:
            self.logger.error(f"Deep validation failed for {endpoint.url}: {e}")
            endpoint.status = EndpointStatus.DISCOVERED
        
        self.store.add_endpoint(endpoint)

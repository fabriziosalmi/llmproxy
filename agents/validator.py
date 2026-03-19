from core.base_agent import BaseAgent
from models import LLMEndpoint, EndpointStatus
from store.store import EndpointStore
import asyncio
import aiohttp
from datetime import datetime

class ValidatorAgent(BaseAgent):
    def __init__(self, store: EndpointStore):
        super().__init__("validator")
        self.store = store

    async def run(self):
        """Main loop for validation."""
        while True:
            self.logger.info("Validating endpoint pool...")
            endpoints = self.store._endpoints # Simplified access
            for endpoint in endpoints:
                if endpoint.status in [EndpointStatus.FOUND, EndpointStatus.VERIFIED]:
                    await self.execute_task(self.validate_endpoint, endpoint)
            
            await asyncio.sleep(300) # Re-validate every 5 min

    async def validate_endpoint(self, endpoint: LLMEndpoint):
        self.logger.info(f"Testing endpoint: {endpoint.url}")
        start_time = datetime.now()
        
        try:
            async with aiohttp.ClientSession() as session:
                # Real LLM interaction: send a simple 'ping' prompt
                payload = {
                    "model": "gpt-3.5-turbo",
                    "messages": [{"role": "user", "content": "ping"}]
                }
                async with session.post(str(endpoint.url), json=payload, timeout=10) as response:
                    latency = (datetime.now() - start_time).total_seconds() * 1000
                    if response.status == 200:
                        endpoint.status = EndpointStatus.VERIFIED
                        endpoint.latency_ms = latency
                        endpoint.last_verified = datetime.now()
                        endpoint.success_rate = (endpoint.success_rate + 1.0) / 2
                        self.logger.info(f"Endpoint {endpoint.url} VALIDATED. Latency: {latency:.2f}ms")
                    else:
                        self.logger.warning(f"Endpoint {endpoint.url} FAILED (status {response.status})")
                        endpoint.status = EndpointStatus.IGNORED
        except Exception as e:
            self.logger.error(f"Error validating {endpoint.url}: {e}")
            endpoint.status = EndpointStatus.IGNORED
            
        self.store.add_endpoint(endpoint)

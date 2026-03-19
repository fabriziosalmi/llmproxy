from core.base_agent import BaseAgent
from models import LLMEndpoint, EndpointStatus
from store.store import EndpointStore
import asyncio

class InterfaceAgent(BaseAgent):
    def __init__(self, store: EndpointStore):
        super().__init__("interface")
        self.store = store

    async def run(self):
        """Main loop for interfacing."""
        while True:
            self.logger.info("Checking discovered endpoints for interfacing...")
            discovered = self.store.get_by_status(EndpointStatus.DISCOVERED)
            for endpoint in discovered:
                await self.execute_task(self.adapt_endpoint, endpoint)
            
            await asyncio.sleep(600) # Check every 10 min

    async def adapt_endpoint(self, endpoint: LLMEndpoint):
        """Analyzes and adapts to the endpoint's specific API requirements."""
        self.logger.info(f"Designing adapter for: {endpoint.url}")
        # Logic to detect if it's OpenAI-compatible, HuggingFace, Gradio, etc.
        # This would generate the necessary headers/body template
        endpoint.provider_type = "openai-compatible" # Mock detection
        endpoint.status = EndpointStatus.VERIFIED # For now, move to verified to be picked up by Validator
        self.store.add_endpoint(endpoint)

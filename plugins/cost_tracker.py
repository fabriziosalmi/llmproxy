import asyncio
from core.base_agent import BaseAgent

class CostTrackerPlugin(BaseAgent):
    """A sample plugin that tracks token usage and costs (Simulated)."""
    
    def __init__(self, **kwargs):
        super().__init__("cost_tracker")
        self.total_tokens = 0
        self.total_cost = 0.0

    async def run(self):
        self.logger.info("Cost Tracker Plugin: Active and monitoring...")
        while True:
            # In a real system, this would listen to an event bus or read from the store
            await asyncio.sleep(60)
            self.logger.info(f"Current Session Cost: ${self.total_cost:.4f} (Tokens: {self.total_tokens})")

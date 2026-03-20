import asyncio
import logging
from typing import Dict, Any, List
from store.store import EndpointStore
from agents.validator import ValidatorAgent
from core.local_assistant import LocalAssistant

logger = logging.getLogger(__name__)

class SelfHealerAgent:
    """Background agent that proactively diagnoses and heals failing endpoints."""
    
    def __init__(self, store: EndpointStore, assistant: LocalAssistant):
        self.store = store
        self.assistant = assistant
        self.validator = ValidatorAgent(store)
        self.running = False

    async def start(self):
        """Starts the periodic self-healing loop."""
        self.running = True
        logger.info("SelfHealerAgent: Started background diagnostics loop")
        while self.running:
            try:
                await self.perform_healing_cycle()
            except Exception as e:
                logger.error(f"SelfHealerAgent: Error in healing cycle: {e}")
            
            # Run every 5 minutes
            await asyncio.sleep(300)

    async def stop(self):
        self.running = False

    async def perform_healing_cycle(self):
        """Identifies and attempts to fix unhealthy endpoints."""
        endpoints = await self.store.get_all()
        unhealthy = [e for e in endpoints if e.status != 1 or (e.success_rate < 0.5 if e.success_rate else False)]
        
        if not unhealthy:
            return

        logger.info(f"SelfHealerAgent: Found {len(unhealthy)} unhealthy endpoints. Starting repair...")
        
        for endpoint in unhealthy:
            await self._heal_endpoint(endpoint)

    async def _heal_endpoint(self, endpoint: Any):
        """Internal logic to diagnose and repair a specific endpoint."""
        logger.info(f"SelfHealerAgent: Diagnosing {endpoint.id} ({endpoint.url})...")
        
        # 1. Simple Re-validation
        result = await self.validator.validate(endpoint.url)
        if result["status"] == "healthy":
            logger.info(f"SelfHealerAgent: Endpoint {endpoint.id} healed via standard re-validation.")
            endpoint.status = 1
            await self.store.update_endpoint(endpoint)
            return

        # 2. AI-Driven Diagnostics
        # Ask the assistant to suggest a fix based on the error
        error_msg = result.get("error", "Unknown error")
        diagnosis_prompt = f"""
        The following LLM endpoint is failing:
        ID: {endpoint.id}
        URL: {endpoint.url}
        Error: {error_msg}
        
        Suggest common fixes if the URL looks slightly wrong (e.g. missing /v1, wrong domain).
        Respond with 'FIX: <new_url>' if you suggest a URL change, otherwise explain why it might be failing.
        """
        
        suggestion = await self.assistant.generate(diagnosis_prompt)
        if suggestion and "FIX:" in suggestion:
            new_url = suggestion.split("FIX:")[1].strip()
            logger.warning(f"SelfHealerAgent: Attempting fix suggested by AI: {new_url}")
            
            # Test the suggested fix
            fix_result = await self.validator.validate(new_url)
            if fix_result["status"] == "healthy":
                logger.info(f"SelfHealerAgent: SUCCESSFULLY HEALED {endpoint.id} with new URL: {new_url}")
                endpoint.url = new_url
                endpoint.status = 1
                await self.store.update_endpoint(endpoint)
                return

        logger.error(f"SelfHealerAgent: Could not heal {endpoint.id}. Remaining unhealthy.")

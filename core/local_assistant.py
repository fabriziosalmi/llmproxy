import aiohttp
import logging
from typing import Dict, Any, Optional, List

logger = logging.getLogger(__name__)

class SafeLoopGuard:
    """Prevents infinite recursion of agents calling LLMs."""
    def __init__(self, max_depth: int = 3):
        self.max_depth = max_depth
        self._depth_map: Dict[str, int] = {}

    def is_safe(self, task_id: str) -> bool:
        return self._depth_map.get(task_id, 0) < self.max_depth

    def increment(self, task_id: str):
        self._depth_map[task_id] = self._depth_map.get(task_id, 0) + 1

    def decrement(self, task_id: str):
        if task_id in self._depth_map:
            self._depth_map[task_id] = max(0, self._depth_map[task_id] - 1)

class LocalAssistant:
    """Interface for on-demand assistance via local LM Studio (localhost:1234)."""
    
    def __init__(self, host: str = "http://localhost:1234", model: str = "smollm-360m-instruct-mlx"):
        self.host = host
        self.model = model
        self.guard = SafeLoopGuard()

    async def consult(self, prompt: str, task_id: str = "default") -> Optional[str]:
        """Consults the local LLM for assistance, subject to safety guards."""
        if not self.guard.is_safe(task_id):
            logger.warning(f"SafeLoopGuard: Blocked potential recursive loop for task {task_id}")
            return None
            
        self.guard.increment(task_id)
        try:
            async with aiohttp.ClientSession() as session:
                payload = {
                    "model": self.model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.7
                }
                async with session.post(f"{self.host}/v1/chat/completions", json=payload, timeout=60) as response:
                    if response.status == 200:
                        data = await response.json()
                        return data["choices"][0]["message"]["content"]
                    else:
                        logger.error(f"LocalAssistant failed: {response.status} - {await response.text()}")
        except Exception as e:
            logger.error(f"LocalAssistant connection error: {e}")
        finally:
            self.guard.decrement(task_id)
            
        return None

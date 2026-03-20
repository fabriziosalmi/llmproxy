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
    
    def __init__(self, host: str = "http://localhost:1234", default_model: str = "smollm-360m-instruct-mlx"):
        self.host = host
        self.default_model = default_model
        self.guard = SafeLoopGuard()

    async def consult(self, prompt: str, task_id: str = "default", model: Optional[str] = None) -> Optional[str]:
        """Consults the local LLM, allowing dynamic model selection."""
        if not self.guard.is_safe(task_id):
            logger.warning(f"SafeLoopGuard: Blocked potential recursive loop for task {task_id}")
            return None
            
        target_model = model or self.default_model
        self.guard.increment(task_id)
        try:
            async with aiohttp.ClientSession() as session:
                payload = {
                    "model": target_model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.7
                }
                logger.info(f"LocalAssistant: Consulting {target_model} for task {task_id}")
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

    async def generate(self, prompt: str, task_id: str = "default", model: Optional[str] = None) -> Optional[str]:
        """Alias for consult() to match common AI interfaces."""
        return await self.consult(prompt, task_id, model)

    async def get_embeddings(self, text: str, model: Optional[str] = None) -> Optional[List[float]]:
        """Generates embeddings for the given text using the local LLM host."""
        target_model = model or self.default_model
        try:
            async with aiohttp.ClientSession() as session:
                payload = {
                    "model": target_model,
                    "input": text
                }
                async with session.post(f"{self.host}/v1/embeddings", json=payload, timeout=30) as response:
                    if response.status == 200:
                        data = await response.json()
                        return data["data"][0]["embedding"]
                    else:
                        logger.error(f"Embeddings failed: {response.status}")
        except Exception as e:
            logger.error(f"Embeddings connection error: {e}")
        return None

    async def consult_vision(self, prompt: str, image_path: str, model: Optional[str] = None) -> Optional[str]:
        """Consults the local vision model with an image."""
        import base64
        import os
        if not os.path.exists(image_path):
            logger.error(f"Vision error: Image not found at {image_path}")
            return None
            
        with open(image_path, "rb") as f:
            base64_image = base64.b64encode(f.read()).decode('utf-8')
            
        target_model = model or self.default_model
        payload = {
            "model": target_model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{base64_image}"}}
                    ]
                }
            ],
            "temperature": 0.0
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(f"{self.host}/v1/chat/completions", json=payload, timeout=90) as response:
                    if response.status == 200:
                        data = await response.json()
                        return data['choices'][0]['message']['content']
                    else:
                        logger.error(f"Vision model failed: {response.status}")
        except Exception as e:
            logger.error(f"Vision connection error: {e}")
            
        return None

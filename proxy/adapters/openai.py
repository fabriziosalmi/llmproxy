import aiohttp
import json
from typing import Dict, Any, AsyncGenerator
from .base import BaseModelAdapter

class OpenAIAdapter(BaseModelAdapter):
    """Adapter for OpenAI-compatible endpoints."""
    
    async def request(self, url: str, body: Dict[str, Any], headers: Dict[str, str], session: aiohttp.ClientSession) -> Any:
        async with session.post(url, json=body, headers=headers) as resp:
            return resp

    async def stream(self, url: str, body: Dict[str, Any], headers: Dict[str, str], session: aiohttp.ClientSession) -> AsyncGenerator[bytes, None]:
        async with session.post(url, json=body, headers=headers) as resp:
            async for chunk in resp.content.iter_any():
                yield chunk

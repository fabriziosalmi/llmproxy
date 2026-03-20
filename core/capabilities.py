import aiohttp
import asyncio
import logging
from typing import Dict, Any, List, Optional
import time

logger = logging.getLogger(__name__)

class CapabilityProber:
    """Probes LLM endpoints for specific capabilities (Vision, Tools, JSON, etc)."""

    @staticmethod
    async def probe_all(url: str, api_key: Optional[str] = None) -> Dict[str, bool]:
        """Runs a suite of probes and returns a capability map."""
        results = await asyncio.gather(
            CapabilityProber.probe_vision(url, api_key),
            CapabilityProber.probe_tools(url, api_key),
            CapabilityProber.probe_json_mode(url, api_key)
        )
        return {
            "vision": results[0],
            "tools": results[1],
            "json_mode": results[2]
        }

    @staticmethod
    async def probe_vision(url: str, api_key: Optional[str] = None) -> bool:
        """Probes for Vision (GPT-4V or compatible)."""
        payload = {
            "model": "auto",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "What is in this image?"},
                        {"type": "image_url", "image_url": {"url": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="}}
                    ]
                }
            ],
            "max_tokens": 10
        }
        return await CapabilityProber._check_payload(url, payload, api_key)

    @staticmethod
    async def probe_tools(url: str, api_key: Optional[str] = None) -> bool:
        """Probes for Tool/Function calling."""
        payload = {
            "model": "auto",
            "messages": [{"role": "user", "content": "What is the weather in Rome?"}],
            "tools": [{
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "parameters": {"type": "object", "properties": {"location": {"type": "string"}}}
                }
            }],
            "tool_choice": "auto"
        }
        return await CapabilityProber._check_payload(url, payload, api_key)

    @staticmethod
    async def probe_json_mode(url: str, api_key: Optional[str] = None) -> bool:
        """Probes for JSON mode constraint."""
        payload = {
            "model": "auto",
            "messages": [{"role": "user", "content": "Respond in JSON: {\"status\": \"ok\"}"}],
            "response_format": {"type": "json_object"}
        }
        return await CapabilityProber._check_payload(url, payload, api_key)

    @staticmethod
    async def _check_payload(url: str, payload: Dict[str, Any], api_key: Optional[str]) -> bool:
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
            
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, timeout=5) as response:
                    # If it accepts the payload (200) or gives a specific 400 about missing model but NOT about payload structure
                    return response.status == 200
        except:
            return False

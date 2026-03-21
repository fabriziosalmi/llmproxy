"""
OpenAI adapter — identity translation (OpenAI is the canonical format).

request() reads the full response body inside the aiohttp context manager and
returns a Starlette Response with .status_code and .body populated — matching
what the rest of the pipeline (shield_sanitizer, cache write, metrics) expects.

stream() yields raw SSE byte chunks while the connection is open.
"""

import aiohttp
from typing import Dict, Any, AsyncGenerator, Tuple
from starlette.responses import Response
from .base import BaseModelAdapter


class OpenAIAdapter(BaseModelAdapter):
    """Adapter for OpenAI and OpenAI-compatible endpoints."""

    provider_name = "openai"

    def translate_request(
        self, base_url: str, body: Dict[str, Any], headers: Dict[str, str],
    ) -> Tuple[str, Dict[str, Any], Dict[str, str]]:
        url = f"{base_url.rstrip('/')}/chat/completions"
        # OpenAI uses Authorization: Bearer <key>
        return url, body, headers

    def translate_response(self, response_data: Dict[str, Any]) -> Dict[str, Any]:
        # Already in OpenAI format
        return response_data

    async def request(
        self,
        url: str,
        body: Dict[str, Any],
        headers: Dict[str, str],
        session: aiohttp.ClientSession,
    ) -> Response:
        async with session.post(url, json=body, headers=headers) as resp:
            content = await resp.read()
            status = resp.status
            content_type = resp.content_type or "application/json"
        return Response(content=content, status_code=status, media_type=content_type)

    async def stream(
        self,
        url: str,
        body: Dict[str, Any],
        headers: Dict[str, str],
        session: aiohttp.ClientSession,
    ) -> AsyncGenerator[bytes, None]:
        async with session.post(url, json=body, headers=headers) as resp:
            async for chunk in resp.content.iter_any():
                yield chunk

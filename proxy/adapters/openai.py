"""
OpenAI-compatible adapter.

request() reads the full response body inside the aiohttp context manager and
returns a Starlette Response with .status_code and .body populated — matching
what the rest of the pipeline (shield_sanitizer, cache write, metrics) expects.

stream() yields raw SSE byte chunks while the connection is open.
"""

import aiohttp
from typing import Dict, Any, AsyncGenerator
from starlette.responses import Response
from .base import BaseModelAdapter


class OpenAIAdapter(BaseModelAdapter):
    """Adapter for OpenAI-compatible endpoints."""

    async def request(
        self,
        url: str,
        body: Dict[str, Any],
        headers: Dict[str, str],
        session: aiohttp.ClientSession,
    ) -> Response:
        """Non-streaming request.

        Reads the full response body before the aiohttp context manager closes,
        then wraps it in a Starlette Response so callers get .status_code / .body.
        """
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
        """Streaming request — yields raw SSE byte chunks."""
        async with session.post(url, json=body, headers=headers) as resp:
            async for chunk in resp.content.iter_any():
                yield chunk

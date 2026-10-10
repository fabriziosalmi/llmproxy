"""
OpenAI adapter — identity translation (OpenAI is the canonical format).

request() reads the full response body inside the aiohttp context manager and
returns a Starlette Response with .status_code and .body populated — matching
what the rest of the pipeline (shield_sanitizer, cache write, metrics) expects.

stream() yields raw SSE byte chunks while the connection is open.
"""

from collections.abc import AsyncGenerator
from typing import Any

import aiohttp
from starlette.responses import Response

from ..http_session import response_timeout
from .base import BaseModelAdapter, raise_for_stream_status

_O_SERIES_PREFIXES = ("o1", "o3", "o4")


def _is_o_series(model: str) -> bool:
    """Check if model is an OpenAI reasoning model (o1/o3/o4 series)."""
    m = model.lower()
    return any(
        m.startswith(p) and (len(m) == len(p) or m[len(p)] in "-_")
        for p in _O_SERIES_PREFIXES
    )


class OpenAIAdapter(BaseModelAdapter):
    """Adapter for OpenAI and OpenAI-compatible endpoints."""

    provider_name = "openai"

    def translate_request(
        self,
        base_url: str,
        body: dict[str, Any],
        headers: dict[str, str],
    ) -> tuple[str, dict[str, Any], dict[str, str]]:
        url = f"{base_url.rstrip('/')}/chat/completions"
        model = body.get("model", "")

        if _is_o_series(model):
            body = dict(body)  # shallow copy to avoid mutating original

            # 1. system → developer role
            messages = []
            for msg in body.get("messages", []):
                if msg.get("role") == "system":
                    messages.append({**msg, "role": "developer"})
                else:
                    messages.append(msg)
            body["messages"] = messages

            # 2. max_tokens → max_completion_tokens
            if "max_tokens" in body and "max_completion_tokens" not in body:
                body["max_completion_tokens"] = body.pop("max_tokens")

            # 3. Remove unsupported params (cause 400 errors)
            for unsupported in (
                "temperature",
                "top_p",
                "frequency_penalty",
                "presence_penalty",
                "logprobs",
                "top_logprobs",
            ):
                body.pop(unsupported, None)

        # Request stream usage data for accurate cost tracking
        if body.get("stream") and "stream_options" not in body:
            body = dict(body) if body is not body else body
            body["stream_options"] = {"include_usage": True}

        return url, body, headers

    def translate_response(self, response_data: dict[str, Any]) -> dict[str, Any]:
        # Already in OpenAI format
        return response_data

    def translate_embedding_request(
        self,
        base_url: str,
        body: dict[str, Any],
        headers: dict[str, str],
    ) -> tuple[str, dict[str, Any], dict[str, str]]:
        url = f"{base_url.rstrip('/')}/embeddings"
        return url, body, headers

    async def request(
        self,
        url: str,
        body: dict[str, Any],
        headers: dict[str, str],
        session: aiohttp.ClientSession,
    ) -> Response:
        async with session.post(
            url, json=body, headers=headers, **response_timeout(session)
        ) as resp:
            content = await resp.read()
            status = resp.status
            content_type = resp.content_type or "application/json"
        return Response(content=content, status_code=status, media_type=content_type)

    async def stream(
        self,
        url: str,
        body: dict[str, Any],
        headers: dict[str, str],
        session: aiohttp.ClientSession,
    ) -> AsyncGenerator[bytes, None]:
        async with session.post(url, json=body, headers=headers) as resp:
            await raise_for_stream_status(resp)
            async for chunk in resp.content.iter_any():
                yield chunk

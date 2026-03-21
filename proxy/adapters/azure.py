"""
Azure OpenAI adapter.

Same request/response format as OpenAI, but different:
  - URL pattern: {resource}.openai.azure.com/openai/deployments/{deployment}/chat/completions?api-version=...
  - Auth: `api-key` header instead of `Authorization: Bearer`
  - Model comes from deployment name in URL, not request body
"""

import aiohttp
from typing import Dict, Any, AsyncGenerator, Tuple
from starlette.responses import Response
from .base import BaseModelAdapter


class AzureAdapter(BaseModelAdapter):
    """Adapter for Azure OpenAI Service."""

    provider_name = "azure"
    DEFAULT_API_VERSION = "2024-10-21"

    def translate_embedding_request(
        self, base_url: str, body: Dict[str, Any], headers: Dict[str, str],
    ) -> Tuple[str, Dict[str, Any], Dict[str, str]]:
        base = base_url.rstrip("/")
        if "/embeddings" not in base:
            url = f"{base}/embeddings?api-version={self.DEFAULT_API_VERSION}"
        else:
            url = base
        azure_headers = dict(headers)
        auth = azure_headers.pop("Authorization", "")
        if auth.startswith("Bearer "):
            azure_headers["api-key"] = auth[7:]
        azure_headers["content-type"] = "application/json"
        return url, body, azure_headers

    def translate_request(
        self, base_url: str, body: Dict[str, Any], headers: Dict[str, str],
    ) -> Tuple[str, Dict[str, Any], Dict[str, str]]:
        # base_url expected format:
        #   https://{resource}.openai.azure.com/openai/deployments/{deployment}
        # or just the resource URL — we append the rest
        base = base_url.rstrip("/")
        if "/chat/completions" not in base:
            api_version = self.DEFAULT_API_VERSION
            url = f"{base}/chat/completions?api-version={api_version}"
        else:
            url = base

        # Azure uses `api-key` header, not Bearer
        azure_headers = dict(headers)
        auth = azure_headers.pop("Authorization", "")
        if auth.startswith("Bearer "):
            azure_headers["api-key"] = auth[7:]
        azure_headers["content-type"] = "application/json"

        # Body is identical to OpenAI — model field is ignored (deployment determines it)
        azure_body = dict(body)

        return url, azure_body, azure_headers

    def translate_response(self, response_data: Dict[str, Any]) -> Dict[str, Any]:
        # Azure returns OpenAI-identical format
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

"""
Base adapter interface for LLM provider communication.

All adapters normalize to OpenAI format on ingress and denormalize
to provider-native format on egress (translation layer pattern).
"""

from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from typing import Any

#: How much of an upstream error body is kept. Error documents are small; the
#: cap only stops a misbehaving upstream from being buffered whole.
_ERROR_BODY_LIMIT = 64 * 1024


class UpstreamStatusError(Exception):
    """The upstream answered a streaming request with an error status.

    A streaming adapter used to hand the body of a 429/503 to the client as if it
    were the first chunk of a stream: the response had already been sent with
    status 200, the circuit breaker saw a successful first chunk, and no fallback
    was tried. Raising this before the first chunk lets the forwarder treat the
    failure exactly as it treats a non-streaming one.
    """

    def __init__(self, status: int, content: bytes, media_type: str):
        super().__init__(f"upstream returned HTTP {status}")
        self.status = status
        self.content = content
        self.media_type = media_type


async def raise_for_stream_status(resp: Any) -> None:
    """Raise UpstreamStatusError (with the upstream body) for an error status."""
    if resp.status < 400:
        return
    chunks: list[bytes] = []
    size = 0
    async for chunk in resp.content.iter_chunked(8192):
        chunks.append(chunk)
        size += len(chunk)
        if size >= _ERROR_BODY_LIMIT:
            break
    raise UpstreamStatusError(
        resp.status, b"".join(chunks), resp.content_type or "application/json"
    )


class BaseModelAdapter(ABC):
    """Abstract interface for model-specific communication."""

    provider_name: str = "base"

    def translate_request(
        self,
        base_url: str,
        body: dict[str, Any],
        headers: dict[str, str],
    ) -> tuple[str, dict[str, Any], dict[str, str]]:
        """Transform OpenAI-format request to provider-native format.

        Returns (full_url, transformed_body, transformed_headers).
        Default: identity transform — subclasses override for provider-specific logic.
        """
        url = f"{base_url.rstrip('/')}/chat/completions"
        return url, body, headers

    def translate_response(self, response_data: dict[str, Any]) -> dict[str, Any]:
        """Transform provider-native response back to OpenAI format.

        Default: identity transform.
        """
        return response_data

    supports_embeddings: bool = True

    def translate_embedding_request(
        self,
        base_url: str,
        body: dict[str, Any],
        headers: dict[str, str],
    ) -> tuple[str, dict[str, Any], dict[str, str]]:
        """Transform OpenAI-format embedding request to provider-native format.

        Returns (full_url, transformed_body, transformed_headers).
        Default: OpenAI /v1/embeddings format.
        """
        url = f"{base_url.rstrip('/')}/embeddings"
        return url, body, headers

    def translate_embedding_response(
        self, response_data: dict[str, Any]
    ) -> dict[str, Any]:
        """Transform provider embedding response back to OpenAI format.

        Default: identity transform.
        """
        return response_data

    def translate_stream_chunk(self, chunk: bytes) -> bytes:
        """Transform a single SSE chunk from provider format to OpenAI SSE format.

        Default: identity transform.
        """
        return chunk

    @abstractmethod
    async def request(
        self,
        url: str,
        body: dict[str, Any],
        headers: dict[str, str],
        session: Any,
    ) -> Any:
        """Sends a non-streaming request."""

    @abstractmethod
    async def stream(
        self,
        url: str,
        body: dict[str, Any],
        headers: dict[str, str],
        session: Any,
    ) -> AsyncGenerator[bytes, None]:
        """Sends a streaming request.

        Must call ``raise_for_stream_status(resp)`` before yielding, so an error
        status surfaces as UpstreamStatusError instead of as stream content.
        """
        yield b""  # pragma: no cover

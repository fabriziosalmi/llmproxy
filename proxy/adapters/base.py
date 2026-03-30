"""
Base adapter interface for LLM provider communication.

All adapters normalize to OpenAI format on ingress and denormalize
to provider-native format on egress (translation layer pattern).
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, AsyncGenerator, Tuple


class BaseModelAdapter(ABC):
    """Abstract interface for model-specific communication."""

    provider_name: str = "base"

    def translate_request(
        self, base_url: str, body: Dict[str, Any], headers: Dict[str, str],
    ) -> Tuple[str, Dict[str, Any], Dict[str, str]]:
        """Transform OpenAI-format request to provider-native format.

        Returns (full_url, transformed_body, transformed_headers).
        Default: identity transform — subclasses override for provider-specific logic.
        """
        url = f"{base_url.rstrip('/')}/chat/completions"
        return url, body, headers

    def translate_response(self, response_data: Dict[str, Any]) -> Dict[str, Any]:
        """Transform provider-native response back to OpenAI format.

        Default: identity transform.
        """
        return response_data

    supports_embeddings: bool = True

    def translate_embedding_request(
        self, base_url: str, body: Dict[str, Any], headers: Dict[str, str],
    ) -> Tuple[str, Dict[str, Any], Dict[str, str]]:
        """Transform OpenAI-format embedding request to provider-native format.

        Returns (full_url, transformed_body, transformed_headers).
        Default: OpenAI /v1/embeddings format.
        """
        url = f"{base_url.rstrip('/')}/embeddings"
        return url, body, headers

    def translate_embedding_response(self, response_data: Dict[str, Any]) -> Dict[str, Any]:
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
        self, url: str, body: Dict[str, Any], headers: Dict[str, str], session: Any,
    ) -> Any:
        """Sends a non-streaming request."""

    @abstractmethod
    async def stream(
        self, url: str, body: Dict[str, Any], headers: Dict[str, str], session: Any,
    ) -> AsyncGenerator[bytes, None]:
        """Sends a streaming request."""
        yield b""  # pragma: no cover

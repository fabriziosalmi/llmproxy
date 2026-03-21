"""
Ollama adapter.

100% OpenAI-compatible at /v1/chat/completions — trivial extension of OpenAI
adapter with Ollama-specific defaults (no auth, localhost:11434).
"""

from typing import Dict, Any, Tuple
from .openai import OpenAIAdapter


class OllamaAdapter(OpenAIAdapter):
    """Adapter for Ollama (local inference server)."""

    provider_name = "ollama"

    def translate_request(
        self, base_url: str, body: Dict[str, Any], headers: Dict[str, str],
    ) -> Tuple[str, Dict[str, Any], Dict[str, str]]:
        # Ollama is OpenAI-compatible — just fix the URL
        base = base_url.rstrip("/")
        if "/v1" not in base:
            base = f"{base}/v1"
        url = f"{base}/chat/completions"

        # Ollama doesn't need auth — strip it to avoid confusion
        ollama_headers = {k: v for k, v in headers.items() if k.lower() != "authorization"}
        ollama_headers["content-type"] = "application/json"

        return url, body, ollama_headers

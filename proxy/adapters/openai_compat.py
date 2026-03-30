"""
Generic OpenAI-compatible adapter (catch-all).

For providers that expose an OpenAI-compatible API with just a different
base URL and API key: Groq, Together, Mistral, Perplexity, xAI, DeepSeek, etc.
"""

from typing import Dict, Any, Tuple
from .openai import OpenAIAdapter


# Provider-specific base URLs (used when endpoint config doesn't specify one)
PROVIDER_DEFAULTS = {
    "groq": "https://api.groq.com/openai/v1",
    "together": "https://api.together.xyz/v1",
    "mistral": "https://api.mistral.ai/v1",
    "perplexity": "https://api.perplexity.ai",
    "xai": "https://api.x.ai/v1",
    "deepseek": "https://api.deepseek.com/v1",
    "fireworks": "https://api.fireworks.ai/inference/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "sambanova": "https://api.sambanova.ai/v1",
}


class OpenAICompatAdapter(OpenAIAdapter):
    """Adapter for any OpenAI-compatible provider."""

    provider_name = "openai-compatible"

    def __init__(self, provider_hint: str = ""):
        self._provider_hint = provider_hint

    def translate_request(
        self, base_url: str, body: Dict[str, Any], headers: Dict[str, str],
    ) -> Tuple[str, Dict[str, Any], Dict[str, str]]:
        # Use provider default URL if base_url looks like a placeholder
        if not base_url or base_url in ("", "http://localhost"):
            base_url = PROVIDER_DEFAULTS.get(self._provider_hint, base_url)

        return super().translate_request(base_url, body, headers)

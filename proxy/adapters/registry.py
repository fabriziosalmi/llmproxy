"""
Provider registry — adapter lookup + model prefix auto-detection.

Resolves a provider name or model string to the correct adapter instance.
"""

import logging
from typing import Optional
from .base import BaseModelAdapter
from .openai import OpenAIAdapter
from .anthropic import AnthropicAdapter
from .google import GoogleAdapter
from .azure import AzureAdapter
from .ollama import OllamaAdapter
from .openai_compat import OpenAICompatAdapter

logger = logging.getLogger("llmproxy.adapters.registry")

# ── Singleton adapter instances ──

_ADAPTERS = {
    "openai": OpenAIAdapter(),
    "anthropic": AnthropicAdapter(),
    "google": GoogleAdapter(),
    "azure": AzureAdapter(),
    "ollama": OllamaAdapter(),
    "groq": OpenAICompatAdapter("groq"),
    "together": OpenAICompatAdapter("together"),
    "mistral": OpenAICompatAdapter("mistral"),
    "perplexity": OpenAICompatAdapter("perplexity"),
    "xai": OpenAICompatAdapter("xai"),
    "deepseek": OpenAICompatAdapter("deepseek"),
    "fireworks": OpenAICompatAdapter("fireworks"),
    "openrouter": OpenAICompatAdapter("openrouter"),
    "sambanova": OpenAICompatAdapter("sambanova"),
    "openai-compatible": OpenAICompatAdapter(),
}

# ── Model prefix → provider mapping ──

MODEL_PREFIXES = [
    # OpenAI
    ("gpt-", "openai"),
    ("o1-", "openai"),
    ("o3-", "openai"),
    ("o4-", "openai"),
    ("chatgpt-", "openai"),
    ("dall-e", "openai"),
    ("whisper", "openai"),
    ("tts-", "openai"),
    # Anthropic
    ("claude-", "anthropic"),
    # Google
    ("gemini-", "google"),
    ("gemma-", "google"),
    # Mistral
    ("mistral-", "mistral"),
    ("mixtral-", "mistral"),
    ("codestral-", "mistral"),
    ("pixtral-", "mistral"),
    ("ministral-", "mistral"),
    # DeepSeek
    ("deepseek-", "deepseek"),
    # xAI
    ("grok-", "xai"),
    # Meta (typically via Ollama, Together, Groq, or Fireworks)
    ("llama-", "ollama"),
    ("llama3", "ollama"),
    # Qwen (typically via Ollama or Together)
    ("qwen", "ollama"),
    # Microsoft
    ("phi-", "ollama"),
    # Cohere (OpenAI-compatible)
    ("command-", "openai-compatible"),
]


def detect_provider(model: str) -> str:
    """Auto-detect provider from model name prefix.

    Returns provider key or 'openai' as default fallback.
    """
    model_lower = model.lower()
    for prefix, provider in MODEL_PREFIXES:
        if model_lower.startswith(prefix):
            return provider
    return "openai"  # sensible default


def get_adapter(provider_type: Optional[str] = None, model: str = "") -> BaseModelAdapter:
    """Resolve adapter by provider type or model name.

    Priority:
      1. Explicit provider_type (from endpoint config)
      2. Auto-detect from model name prefix
      3. Fall back to OpenAI adapter
    """
    if provider_type and provider_type in _ADAPTERS:
        return _ADAPTERS[provider_type]

    if model:
        detected = detect_provider(model)
        adapter = _ADAPTERS.get(detected)
        if adapter:
            logger.debug(f"Auto-detected provider '{detected}' for model '{model}'")
            return adapter

    return _ADAPTERS["openai"]


# ── Provider metadata (for UI/API) ──

SUPPORTED_PROVIDERS = {
    "openai": {"name": "OpenAI", "auth": "bearer", "base_url": "https://api.openai.com/v1"},
    "anthropic": {"name": "Anthropic", "auth": "x-api-key", "base_url": "https://api.anthropic.com/v1"},
    "google": {"name": "Google Gemini", "auth": "bearer/api-key", "base_url": "https://generativelanguage.googleapis.com/v1beta"},
    "azure": {"name": "Azure OpenAI", "auth": "api-key", "base_url": "https://{resource}.openai.azure.com/openai/deployments/{deployment}"},
    "ollama": {"name": "Ollama", "auth": "none", "base_url": "http://localhost:11434"},
    "groq": {"name": "Groq", "auth": "bearer", "base_url": "https://api.groq.com/openai/v1"},
    "together": {"name": "Together AI", "auth": "bearer", "base_url": "https://api.together.xyz/v1"},
    "mistral": {"name": "Mistral AI", "auth": "bearer", "base_url": "https://api.mistral.ai/v1"},
    "perplexity": {"name": "Perplexity", "auth": "bearer", "base_url": "https://api.perplexity.ai"},
    "xai": {"name": "xAI", "auth": "bearer", "base_url": "https://api.x.ai/v1"},
    "deepseek": {"name": "DeepSeek", "auth": "bearer", "base_url": "https://api.deepseek.com/v1"},
    "fireworks": {"name": "Fireworks AI", "auth": "bearer", "base_url": "https://api.fireworks.ai/inference/v1"},
    "openrouter": {"name": "OpenRouter", "auth": "bearer", "base_url": "https://openrouter.ai/api/v1"},
    "sambanova": {"name": "SambaNova", "auth": "bearer", "base_url": "https://api.sambanova.ai/v1"},
    "openai-compatible": {"name": "OpenAI-Compatible", "auth": "bearer", "base_url": ""},
}

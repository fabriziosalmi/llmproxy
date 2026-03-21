from .base import BaseModelAdapter
from .openai import OpenAIAdapter
from .anthropic import AnthropicAdapter
from .google import GoogleAdapter
from .azure import AzureAdapter
from .ollama import OllamaAdapter
from .openai_compat import OpenAICompatAdapter
from .registry import get_adapter, detect_provider, SUPPORTED_PROVIDERS

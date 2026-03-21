"""
Tests for multi-provider adapter translation layer.

Covers: request translation, response translation, stream chunk translation,
model prefix auto-detection, and provider registry resolution.
"""

import json
import pytest

from proxy.adapters.base import BaseModelAdapter
from proxy.adapters.openai import OpenAIAdapter
from proxy.adapters.anthropic import AnthropicAdapter
from proxy.adapters.google import GoogleAdapter
from proxy.adapters.azure import AzureAdapter
from proxy.adapters.ollama import OllamaAdapter
from proxy.adapters.openai_compat import OpenAICompatAdapter
from proxy.adapters.registry import get_adapter, detect_provider, SUPPORTED_PROVIDERS


# ── Fixtures ──

OPENAI_REQUEST = {
    "model": "gpt-4o",
    "messages": [
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi!"},
        {"role": "user", "content": "How are you?"},
    ],
    "temperature": 0.7,
    "max_tokens": 1000,
    "stream": False,
}

OPENAI_HEADERS = {
    "Authorization": "Bearer sk-test-key-123",
    "Content-Type": "application/json",
}

OPENAI_RESPONSE = {
    "id": "chatcmpl-123",
    "object": "chat.completion",
    "created": 1700000000,
    "model": "gpt-4o",
    "choices": [{
        "index": 0,
        "message": {"role": "assistant", "content": "I'm doing well!"},
        "finish_reason": "stop",
    }],
    "usage": {"prompt_tokens": 20, "completion_tokens": 5, "total_tokens": 25},
}


# ══════════════════════════════════════════════════════
# OpenAI Adapter (identity transform)
# ══════════════════════════════════════════════════════

class TestOpenAIAdapter:
    def setup_method(self):
        self.adapter = OpenAIAdapter()

    def test_provider_name(self):
        assert self.adapter.provider_name == "openai"

    def test_translate_request_url(self):
        url, body, headers = self.adapter.translate_request(
            "https://api.openai.com/v1", OPENAI_REQUEST, OPENAI_HEADERS,
        )
        assert url == "https://api.openai.com/v1/chat/completions"
        assert body is OPENAI_REQUEST  # identity — same object
        assert headers is OPENAI_HEADERS

    def test_translate_request_trailing_slash(self):
        url, _, _ = self.adapter.translate_request(
            "https://api.openai.com/v1/", OPENAI_REQUEST, OPENAI_HEADERS,
        )
        assert url == "https://api.openai.com/v1/chat/completions"

    def test_translate_response_identity(self):
        result = self.adapter.translate_response(OPENAI_RESPONSE)
        assert result is OPENAI_RESPONSE  # identity


# ══════════════════════════════════════════════════════
# Anthropic Adapter
# ══════════════════════════════════════════════════════

class TestAnthropicAdapter:
    def setup_method(self):
        self.adapter = AnthropicAdapter()

    def test_provider_name(self):
        assert self.adapter.provider_name == "anthropic"

    def test_translate_request_url(self):
        url, body, headers = self.adapter.translate_request(
            "https://api.anthropic.com/v1", OPENAI_REQUEST, dict(OPENAI_HEADERS),
        )
        assert url == "https://api.anthropic.com/v1/messages"

    def test_translate_request_system_extraction(self):
        _, body, _ = self.adapter.translate_request(
            "https://api.anthropic.com/v1", OPENAI_REQUEST, dict(OPENAI_HEADERS),
        )
        # System message extracted to top-level
        assert body["system"] == "You are helpful."
        # System message removed from messages array
        roles = [m["role"] for m in body["messages"]]
        assert "system" not in roles
        assert roles == ["user", "assistant", "user"]

    def test_translate_request_max_tokens_required(self):
        _, body, _ = self.adapter.translate_request(
            "https://api.anthropic.com/v1", OPENAI_REQUEST, dict(OPENAI_HEADERS),
        )
        assert body["max_tokens"] == 1000

    def test_translate_request_max_tokens_default(self):
        req = {"model": "claude-sonnet-4-20250514", "messages": [{"role": "user", "content": "Hi"}]}
        _, body, _ = self.adapter.translate_request(
            "https://api.anthropic.com/v1", req, dict(OPENAI_HEADERS),
        )
        assert body["max_tokens"] == 4096

    def test_translate_request_auth_header(self):
        _, _, headers = self.adapter.translate_request(
            "https://api.anthropic.com/v1", OPENAI_REQUEST, dict(OPENAI_HEADERS),
        )
        assert "Authorization" not in headers
        assert headers["x-api-key"] == "sk-test-key-123"
        assert headers["anthropic-version"] == "2023-06-01"

    def test_translate_request_temperature(self):
        _, body, _ = self.adapter.translate_request(
            "https://api.anthropic.com/v1", OPENAI_REQUEST, dict(OPENAI_HEADERS),
        )
        assert body["temperature"] == 0.7

    def test_translate_request_stop_sequences(self):
        req = dict(OPENAI_REQUEST, stop=["END", "STOP"])
        _, body, _ = self.adapter.translate_request(
            "https://api.anthropic.com/v1", req, dict(OPENAI_HEADERS),
        )
        assert body["stop_sequences"] == ["END", "STOP"]

    def test_translate_request_stop_string(self):
        req = dict(OPENAI_REQUEST, stop="END")
        _, body, _ = self.adapter.translate_request(
            "https://api.anthropic.com/v1", req, dict(OPENAI_HEADERS),
        )
        assert body["stop_sequences"] == ["END"]

    def test_translate_request_stream(self):
        req = dict(OPENAI_REQUEST, stream=True)
        _, body, _ = self.adapter.translate_request(
            "https://api.anthropic.com/v1", req, dict(OPENAI_HEADERS),
        )
        assert body["stream"] is True

    def test_translate_request_no_system(self):
        req = {"model": "claude-sonnet-4-20250514", "messages": [{"role": "user", "content": "Hi"}]}
        _, body, _ = self.adapter.translate_request(
            "https://api.anthropic.com/v1", req, dict(OPENAI_HEADERS),
        )
        assert "system" not in body

    def test_translate_response_text(self):
        anthropic_response = {
            "id": "msg_123",
            "type": "message",
            "role": "assistant",
            "model": "claude-sonnet-4-20250514",
            "content": [{"type": "text", "text": "Hello!"}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }
        result = self.adapter.translate_response(anthropic_response)

        assert result["object"] == "chat.completion"
        assert result["id"] == "msg_123"
        assert result["choices"][0]["message"]["content"] == "Hello!"
        assert result["choices"][0]["finish_reason"] == "stop"
        assert result["usage"]["prompt_tokens"] == 10
        assert result["usage"]["completion_tokens"] == 5
        assert result["usage"]["total_tokens"] == 15

    def test_translate_response_tool_use(self):
        anthropic_response = {
            "id": "msg_456",
            "model": "claude-sonnet-4-20250514",
            "content": [
                {"type": "text", "text": "Let me check."},
                {"type": "tool_use", "id": "call_1", "name": "get_weather", "input": {"city": "SF"}},
            ],
            "stop_reason": "tool_use",
            "usage": {"input_tokens": 20, "output_tokens": 10},
        }
        result = self.adapter.translate_response(anthropic_response)

        assert result["choices"][0]["message"]["content"] == "Let me check."
        assert result["choices"][0]["finish_reason"] == "tool_calls"
        tool_calls = result["choices"][0]["message"]["tool_calls"]
        assert len(tool_calls) == 1
        assert tool_calls[0]["function"]["name"] == "get_weather"
        assert json.loads(tool_calls[0]["function"]["arguments"]) == {"city": "SF"}

    def test_translate_response_max_tokens(self):
        anthropic_response = {
            "id": "msg_789",
            "model": "claude-sonnet-4-20250514",
            "content": [{"type": "text", "text": "Truncated..."}],
            "stop_reason": "max_tokens",
            "usage": {"input_tokens": 5, "output_tokens": 100},
        }
        result = self.adapter.translate_response(anthropic_response)
        assert result["choices"][0]["finish_reason"] == "length"

    def test_translate_response_error_passthrough(self):
        error_resp = {"error": {"type": "rate_limit", "message": "Too many requests"}}
        result = self.adapter.translate_response(error_resp)
        assert result == error_resp

    def test_translate_stream_content_delta(self):
        chunk = b'event: content_block_delta\ndata: {"type":"content_block_delta","delta":{"type":"text_delta","text":"Hi"}}\n\n'
        result = self.adapter.translate_stream_chunk(chunk)
        assert b'"chat.completion.chunk"' in result
        data = json.loads(result.decode().split("data: ")[1].strip())
        assert data["choices"][0]["delta"]["content"] == "Hi"

    def test_translate_stream_message_stop(self):
        chunk = b'event: message_stop\ndata: {"type":"message_stop"}\n\n'
        result = self.adapter.translate_stream_chunk(chunk)
        assert b"[DONE]" in result

    def test_translate_tools_openai_to_anthropic(self):
        from proxy.adapters.anthropic import _translate_tools
        openai_tools = [{
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Get weather for a city",
                "parameters": {"type": "object", "properties": {"city": {"type": "string"}}},
            },
        }]
        result = _translate_tools(openai_tools)
        assert len(result) == 1
        assert result[0]["name"] == "get_weather"
        assert result[0]["description"] == "Get weather for a city"
        assert "input_schema" in result[0]

    def test_translate_tool_choice(self):
        from proxy.adapters.anthropic import _translate_tool_choice
        assert _translate_tool_choice("auto") == {"type": "auto"}
        assert _translate_tool_choice("none") == {"type": "none"}
        assert _translate_tool_choice("required") == {"type": "any"}
        result = _translate_tool_choice({"type": "function", "function": {"name": "foo"}})
        assert result == {"type": "tool", "name": "foo"}


# ══════════════════════════════════════════════════════
# Google Gemini Adapter
# ══════════════════════════════════════════════════════

class TestGoogleAdapter:
    def setup_method(self):
        self.adapter = GoogleAdapter()

    def test_provider_name(self):
        assert self.adapter.provider_name == "google"

    def test_translate_request_url(self):
        url, _, _ = self.adapter.translate_request(
            "https://generativelanguage.googleapis.com/v1beta",
            {"model": "gemini-2.5-flash", "messages": [{"role": "user", "content": "Hi"}]},
            dict(OPENAI_HEADERS),
        )
        assert "models/gemini-2.5-flash:generateContent" in url

    def test_translate_request_streaming_url(self):
        url, _, _ = self.adapter.translate_request(
            "https://generativelanguage.googleapis.com/v1beta",
            {"model": "gemini-2.5-flash", "messages": [{"role": "user", "content": "Hi"}], "stream": True},
            dict(OPENAI_HEADERS),
        )
        assert "streamGenerateContent" in url
        assert "alt=sse" in url

    def test_translate_request_api_key_in_url(self):
        url, _, headers = self.adapter.translate_request(
            "https://generativelanguage.googleapis.com/v1beta",
            {"model": "gemini-2.5-flash", "messages": [{"role": "user", "content": "Hi"}]},
            dict(OPENAI_HEADERS),
        )
        assert "key=sk-test-key-123" in url
        assert "Authorization" not in headers

    def test_translate_request_messages_to_contents(self):
        _, body, _ = self.adapter.translate_request(
            "https://generativelanguage.googleapis.com/v1beta",
            OPENAI_REQUEST,
            dict(OPENAI_HEADERS),
        )
        assert "contents" in body
        assert "messages" not in body
        # Check role translation
        roles = [c["role"] for c in body["contents"]]
        assert "model" in roles  # assistant → model
        assert "user" in roles

    def test_translate_request_system_instruction(self):
        _, body, _ = self.adapter.translate_request(
            "https://generativelanguage.googleapis.com/v1beta",
            OPENAI_REQUEST,
            dict(OPENAI_HEADERS),
        )
        assert "systemInstruction" in body
        assert body["systemInstruction"]["parts"][0]["text"] == "You are helpful."
        # System not in contents
        for content in body["contents"]:
            assert content["role"] != "system"

    def test_translate_request_generation_config(self):
        _, body, _ = self.adapter.translate_request(
            "https://generativelanguage.googleapis.com/v1beta",
            OPENAI_REQUEST,
            dict(OPENAI_HEADERS),
        )
        gen_config = body["generationConfig"]
        assert gen_config["temperature"] == 0.7
        assert gen_config["maxOutputTokens"] == 1000

    def test_translate_request_no_system(self):
        req = {"model": "gemini-2.5-flash", "messages": [{"role": "user", "content": "Hi"}]}
        _, body, _ = self.adapter.translate_request(
            "https://generativelanguage.googleapis.com/v1beta", req, {},
        )
        assert "systemInstruction" not in body

    def test_translate_response(self):
        gemini_response = {
            "candidates": [{
                "content": {"parts": [{"text": "Hello!"}], "role": "model"},
                "finishReason": "STOP",
            }],
            "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5, "totalTokenCount": 15},
        }
        result = self.adapter.translate_response(gemini_response)
        assert result["object"] == "chat.completion"
        assert result["choices"][0]["message"]["content"] == "Hello!"
        assert result["choices"][0]["finish_reason"] == "stop"
        assert result["usage"]["prompt_tokens"] == 10

    def test_translate_response_tool_call(self):
        gemini_response = {
            "candidates": [{
                "content": {
                    "parts": [{"functionCall": {"name": "search", "args": {"q": "test"}}}],
                    "role": "model",
                },
                "finishReason": "STOP",
            }],
            "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 5, "totalTokenCount": 10},
        }
        result = self.adapter.translate_response(gemini_response)
        tool_calls = result["choices"][0]["message"]["tool_calls"]
        assert len(tool_calls) == 1
        assert tool_calls[0]["function"]["name"] == "search"

    def test_translate_response_empty_candidates(self):
        result = self.adapter.translate_response({"candidates": []})
        assert "error" in result

    def test_translate_response_error(self):
        error = {"error": {"message": "Bad request"}}
        assert self.adapter.translate_response(error) == error

    def test_translate_response_safety_block(self):
        gemini_response = {
            "candidates": [{
                "content": {"parts": [{"text": "..."}], "role": "model"},
                "finishReason": "SAFETY",
            }],
            "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 0, "totalTokenCount": 5},
        }
        result = self.adapter.translate_response(gemini_response)
        assert result["choices"][0]["finish_reason"] == "content_filter"


# ══════════════════════════════════════════════════════
# Azure Adapter
# ══════════════════════════════════════════════════════

class TestAzureAdapter:
    def setup_method(self):
        self.adapter = AzureAdapter()

    def test_provider_name(self):
        assert self.adapter.provider_name == "azure"

    def test_translate_request_url(self):
        url, _, _ = self.adapter.translate_request(
            "https://myresource.openai.azure.com/openai/deployments/gpt4o",
            OPENAI_REQUEST,
            dict(OPENAI_HEADERS),
        )
        assert "chat/completions" in url
        assert "api-version=" in url

    def test_translate_request_auth_header(self):
        _, _, headers = self.adapter.translate_request(
            "https://myresource.openai.azure.com/openai/deployments/gpt4o",
            OPENAI_REQUEST,
            dict(OPENAI_HEADERS),
        )
        assert "Authorization" not in headers
        assert headers["api-key"] == "sk-test-key-123"

    def test_translate_response_identity(self):
        # Azure returns OpenAI-identical format
        result = self.adapter.translate_response(OPENAI_RESPONSE)
        assert result is OPENAI_RESPONSE


# ══════════════════════════════════════════════════════
# Ollama Adapter
# ══════════════════════════════════════════════════════

class TestOllamaAdapter:
    def setup_method(self):
        self.adapter = OllamaAdapter()

    def test_provider_name(self):
        assert self.adapter.provider_name == "ollama"

    def test_translate_request_url_appends_v1(self):
        url, _, _ = self.adapter.translate_request(
            "http://localhost:11434",
            OPENAI_REQUEST,
            dict(OPENAI_HEADERS),
        )
        assert url == "http://localhost:11434/v1/chat/completions"

    def test_translate_request_url_already_has_v1(self):
        url, _, _ = self.adapter.translate_request(
            "http://localhost:11434/v1",
            OPENAI_REQUEST,
            dict(OPENAI_HEADERS),
        )
        assert url == "http://localhost:11434/v1/chat/completions"

    def test_translate_request_strips_auth(self):
        _, _, headers = self.adapter.translate_request(
            "http://localhost:11434",
            OPENAI_REQUEST,
            dict(OPENAI_HEADERS),
        )
        assert "authorization" not in {k.lower() for k in headers}


# ══════════════════════════════════════════════════════
# OpenAI-Compatible Adapter (catch-all)
# ══════════════════════════════════════════════════════

class TestOpenAICompatAdapter:
    def test_provider_name(self):
        adapter = OpenAICompatAdapter()
        assert adapter.provider_name == "openai-compatible"

    def test_groq_default_url(self):
        adapter = OpenAICompatAdapter("groq")
        url, _, _ = adapter.translate_request("", OPENAI_REQUEST, dict(OPENAI_HEADERS))
        assert "api.groq.com" in url

    def test_together_default_url(self):
        adapter = OpenAICompatAdapter("together")
        url, _, _ = adapter.translate_request("", OPENAI_REQUEST, dict(OPENAI_HEADERS))
        assert "api.together.xyz" in url

    def test_explicit_url_overrides_default(self):
        adapter = OpenAICompatAdapter("groq")
        url, _, _ = adapter.translate_request(
            "https://custom.api.com/v1", OPENAI_REQUEST, dict(OPENAI_HEADERS),
        )
        assert "custom.api.com" in url


# ══════════════════════════════════════════════════════
# Model Prefix Auto-Detection
# ══════════════════════════════════════════════════════

class TestDetectProvider:
    def test_openai_models(self):
        assert detect_provider("gpt-4o") == "openai"
        assert detect_provider("gpt-4o-mini") == "openai"
        assert detect_provider("o3-mini") == "openai"
        assert detect_provider("o1-preview") == "openai"

    def test_anthropic_models(self):
        assert detect_provider("claude-sonnet-4-20250514") == "anthropic"
        assert detect_provider("claude-haiku-4-5-20251001") == "anthropic"
        assert detect_provider("claude-opus-4-20250514") == "anthropic"

    def test_google_models(self):
        assert detect_provider("gemini-2.5-pro") == "google"
        assert detect_provider("gemini-2.5-flash") == "google"
        assert detect_provider("gemma-2-9b") == "google"

    def test_mistral_models(self):
        assert detect_provider("mistral-large-latest") == "mistral"
        assert detect_provider("mixtral-8x7b-32768") == "mistral"
        assert detect_provider("codestral-latest") == "mistral"

    def test_deepseek_models(self):
        assert detect_provider("deepseek-chat") == "deepseek"
        assert detect_provider("deepseek-reasoner") == "deepseek"

    def test_xai_models(self):
        assert detect_provider("grok-3") == "xai"
        assert detect_provider("grok-3-mini") == "xai"

    def test_ollama_local_models(self):
        assert detect_provider("llama3.3") == "ollama"
        assert detect_provider("llama-3.3-70b") == "ollama"
        assert detect_provider("qwen3") == "ollama"
        assert detect_provider("phi-4") == "ollama"

    def test_unknown_defaults_to_openai(self):
        assert detect_provider("some-custom-model") == "openai"
        assert detect_provider("") == "openai"

    def test_case_insensitive(self):
        assert detect_provider("GPT-4o") == "openai"
        assert detect_provider("Claude-Sonnet-4") == "anthropic"
        assert detect_provider("GEMINI-2.5-pro") == "google"


# ══════════════════════════════════════════════════════
# Provider Registry
# ══════════════════════════════════════════════════════

class TestProviderRegistry:
    def test_get_adapter_by_provider_type(self):
        adapter = get_adapter("anthropic")
        assert isinstance(adapter, AnthropicAdapter)

    def test_get_adapter_by_model_auto_detect(self):
        adapter = get_adapter(model="claude-sonnet-4-20250514")
        assert isinstance(adapter, AnthropicAdapter)

    def test_get_adapter_provider_overrides_model(self):
        # Explicit provider wins over model prefix
        adapter = get_adapter("openai", model="claude-sonnet-4-20250514")
        assert isinstance(adapter, OpenAIAdapter)

    def test_get_adapter_default_openai(self):
        adapter = get_adapter()
        assert isinstance(adapter, OpenAIAdapter)

    def test_get_adapter_unknown_provider_falls_back(self):
        adapter = get_adapter("nonexistent-provider")
        assert isinstance(adapter, OpenAIAdapter)

    def test_all_supported_providers_have_metadata(self):
        for provider in SUPPORTED_PROVIDERS:
            info = SUPPORTED_PROVIDERS[provider]
            assert "name" in info
            assert "auth" in info
            assert "base_url" in info

    def test_supported_providers_count(self):
        # We should have at least 14 providers
        assert len(SUPPORTED_PROVIDERS) >= 14

    def test_get_adapter_google(self):
        adapter = get_adapter("google")
        assert isinstance(adapter, GoogleAdapter)

    def test_get_adapter_azure(self):
        adapter = get_adapter("azure")
        assert isinstance(adapter, AzureAdapter)

    def test_get_adapter_ollama(self):
        adapter = get_adapter("ollama")
        assert isinstance(adapter, OllamaAdapter)

    def test_get_adapter_groq(self):
        adapter = get_adapter("groq")
        assert isinstance(adapter, OpenAICompatAdapter)

    def test_get_adapter_together(self):
        adapter = get_adapter("together")
        assert isinstance(adapter, OpenAICompatAdapter)


# ══════════════════════════════════════════════════════
# Edge Cases
# ══════════════════════════════════════════════════════

class TestEdgeCases:
    def test_anthropic_multipart_content(self):
        """Multiple text blocks in Anthropic response."""
        adapter = AnthropicAdapter()
        resp = {
            "id": "msg_multi",
            "model": "claude-sonnet-4-20250514",
            "content": [
                {"type": "text", "text": "Part 1."},
                {"type": "text", "text": "Part 2."},
            ],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 5, "output_tokens": 10},
        }
        result = adapter.translate_response(resp)
        assert result["choices"][0]["message"]["content"] == "Part 1.\nPart 2."

    def test_google_multimodal_content(self):
        """Multimodal content (text + image) in Google request."""
        adapter = GoogleAdapter()
        req = {
            "model": "gemini-2.5-flash",
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": "What's in this image?"},
                    {"type": "image_url", "image_url": {"url": "https://example.com/cat.jpg"}},
                ],
            }],
        }
        _, body, _ = adapter.translate_request(
            "https://generativelanguage.googleapis.com/v1beta", req, {},
        )
        parts = body["contents"][0]["parts"]
        assert len(parts) == 2
        assert parts[0]["text"] == "What's in this image?"

    def test_azure_url_already_complete(self):
        """Azure URL that already includes chat/completions."""
        adapter = AzureAdapter()
        url, _, _ = adapter.translate_request(
            "https://my.azure.com/openai/deployments/gpt4/chat/completions?api-version=2024-10-21",
            OPENAI_REQUEST,
            dict(OPENAI_HEADERS),
        )
        # Should not double-append
        assert url.count("chat/completions") == 1

    def test_anthropic_max_completion_tokens_alias(self):
        """max_completion_tokens (OpenAI v2 name) → max_tokens."""
        adapter = AnthropicAdapter()
        req = {"model": "claude-sonnet-4-20250514", "messages": [{"role": "user", "content": "Hi"}], "max_completion_tokens": 2048}
        _, body, _ = adapter.translate_request(
            "https://api.anthropic.com/v1", req, {},
        )
        assert body["max_tokens"] == 2048

    def test_empty_messages(self):
        """Handle empty messages array gracefully."""
        adapter = AnthropicAdapter()
        req = {"model": "claude-sonnet-4-20250514", "messages": []}
        _, body, _ = adapter.translate_request(
            "https://api.anthropic.com/v1", req, {},
        )
        assert body["messages"] == []
        assert "system" not in body

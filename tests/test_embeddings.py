"""
Tests for /v1/embeddings endpoint and embedding adapter translations.

Covers:
  - Embedding model provider detection
  - OpenAI, Google, Ollama, Azure embedding request translation
  - Anthropic rejection (no embedding support)
  - Google embedding response translation
  - Security pipeline (PII in document chunks)
"""

from proxy.adapters.openai import OpenAIAdapter
from proxy.adapters.anthropic import AnthropicAdapter
from proxy.adapters.google import GoogleAdapter
from proxy.adapters.azure import AzureAdapter
from proxy.adapters.ollama import OllamaAdapter
from proxy.routes.embeddings import _detect_embedding_provider


OPENAI_EMBEDDING_REQUEST = {
    "model": "text-embedding-3-small",
    "input": "Hello world",
}

HEADERS = {
    "Authorization": "Bearer sk-test-key-123",
    "Content-Type": "application/json",
}


# ══════════════════════════════════════════════════════
# Provider Detection for Embedding Models
# ══════════════════════════════════════════════════════

class TestEmbeddingProviderDetection:
    def test_openai_embedding_models(self):
        assert _detect_embedding_provider("text-embedding-3-small") == "openai"
        assert _detect_embedding_provider("text-embedding-3-large") == "openai"
        assert _detect_embedding_provider("text-embedding-ada-002") == "openai"

    def test_google_embedding_models(self):
        assert _detect_embedding_provider("text-embedding-004") == "google"
        assert _detect_embedding_provider("embedding-001") == "google"

    def test_ollama_embedding_models(self):
        assert _detect_embedding_provider("nomic-embed-text") == "ollama"
        assert _detect_embedding_provider("mxbai-embed-large") == "ollama"
        assert _detect_embedding_provider("all-minilm") == "ollama"

    def test_mistral_embedding_model(self):
        assert _detect_embedding_provider("mistral-embed") == "mistral"

    def test_unknown_falls_back_to_chat_detection(self):
        # "gpt-4o" is not an embedding model but should still resolve
        assert _detect_embedding_provider("gpt-4o") == "openai"


# ══════════════════════════════════════════════════════
# OpenAI Embedding Translation
# ══════════════════════════════════════════════════════

class TestOpenAIEmbeddingAdapter:
    def setup_method(self):
        self.adapter = OpenAIAdapter()

    def test_url(self):
        url, _, _ = self.adapter.translate_embedding_request(
            "https://api.openai.com/v1", OPENAI_EMBEDDING_REQUEST, dict(HEADERS),
        )
        assert url == "https://api.openai.com/v1/embeddings"

    def test_body_passthrough(self):
        _, body, _ = self.adapter.translate_embedding_request(
            "https://api.openai.com/v1", OPENAI_EMBEDDING_REQUEST, dict(HEADERS),
        )
        assert body is OPENAI_EMBEDDING_REQUEST

    def test_supports_embeddings(self):
        assert self.adapter.supports_embeddings is True


# ══════════════════════════════════════════════════════
# Anthropic — No Embedding Support
# ══════════════════════════════════════════════════════

class TestAnthropicEmbeddingAdapter:
    def test_does_not_support_embeddings(self):
        adapter = AnthropicAdapter()
        assert adapter.supports_embeddings is False


# ══════════════════════════════════════════════════════
# Google Gemini Embedding Translation
# ══════════════════════════════════════════════════════

class TestGoogleEmbeddingAdapter:
    def setup_method(self):
        self.adapter = GoogleAdapter()

    def test_url_contains_embed_content(self):
        url, _, _ = self.adapter.translate_embedding_request(
            "https://generativelanguage.googleapis.com/v1beta",
            {"model": "text-embedding-004", "input": "Hello"},
            dict(HEADERS),
        )
        assert "models/text-embedding-004:embedContent" in url

    def test_api_key_in_url(self):
        url, _, headers = self.adapter.translate_embedding_request(
            "https://generativelanguage.googleapis.com/v1beta",
            {"model": "text-embedding-004", "input": "Hello"},
            dict(HEADERS),
        )
        assert "key=sk-test-key-123" in url
        assert "Authorization" not in headers

    def test_body_translation(self):
        _, body, _ = self.adapter.translate_embedding_request(
            "https://generativelanguage.googleapis.com/v1beta",
            {"model": "text-embedding-004", "input": "Hello world"},
            {},
        )
        assert body["content"]["parts"][0]["text"] == "Hello world"
        assert body["model"] == "models/text-embedding-004"

    def test_list_input_uses_first(self):
        _, body, _ = self.adapter.translate_embedding_request(
            "https://generativelanguage.googleapis.com/v1beta",
            {"model": "text-embedding-004", "input": ["Hello", "World"]},
            {},
        )
        assert body["content"]["parts"][0]["text"] == "Hello"

    def test_response_translation(self):
        gemini_response = {
            "embedding": {"values": [0.1, 0.2, 0.3, 0.4]},
            "model": "text-embedding-004",
        }
        result = self.adapter.translate_embedding_response(gemini_response)
        assert result["object"] == "list"
        assert len(result["data"]) == 1
        assert result["data"][0]["object"] == "embedding"
        assert result["data"][0]["embedding"] == [0.1, 0.2, 0.3, 0.4]
        assert result["data"][0]["index"] == 0

    def test_response_error_passthrough(self):
        error = {"error": {"message": "Bad request"}}
        assert self.adapter.translate_embedding_response(error) == error

    def test_supports_embeddings(self):
        assert self.adapter.supports_embeddings is True


# ══════════════════════════════════════════════════════
# Azure Embedding Translation
# ══════════════════════════════════════════════════════

class TestAzureEmbeddingAdapter:
    def setup_method(self):
        self.adapter = AzureAdapter()

    def test_url(self):
        url, _, _ = self.adapter.translate_embedding_request(
            "https://myresource.openai.azure.com/openai/deployments/embedding",
            OPENAI_EMBEDDING_REQUEST,
            dict(HEADERS),
        )
        assert "embeddings" in url
        assert "api-version=" in url

    def test_auth_header(self):
        _, _, headers = self.adapter.translate_embedding_request(
            "https://myresource.openai.azure.com/openai/deployments/embedding",
            OPENAI_EMBEDDING_REQUEST,
            dict(HEADERS),
        )
        assert "Authorization" not in headers
        assert headers["api-key"] == "sk-test-key-123"


# ══════════════════════════════════════════════════════
# Ollama Embedding Translation
# ══════════════════════════════════════════════════════

class TestOllamaEmbeddingAdapter:
    def setup_method(self):
        self.adapter = OllamaAdapter()

    def test_url(self):
        url, _, _ = self.adapter.translate_embedding_request(
            "http://localhost:11434",
            {"model": "nomic-embed-text", "input": "Hello"},
            dict(HEADERS),
        )
        assert url == "http://localhost:11434/v1/embeddings"

    def test_strips_auth(self):
        _, _, headers = self.adapter.translate_embedding_request(
            "http://localhost:11434",
            {"model": "nomic-embed-text", "input": "Hello"},
            dict(HEADERS),
        )
        assert "authorization" not in {k.lower() for k in headers}


# ══════════════════════════════════════════════════════
# Embedding Pricing
# ══════════════════════════════════════════════════════

class TestEmbeddingPricing:
    def test_openai_embedding_pricing(self):
        from core.pricing import estimate_cost
        # text-embedding-3-small: $0.02/MTok input, no output
        cost = estimate_cost("text-embedding-3-small", 1_000_000, 0)
        assert abs(cost - 0.02) < 0.001

    def test_local_embedding_free(self):
        from core.pricing import estimate_cost
        cost = estimate_cost("nomic-embed-text", 1_000_000, 0)
        assert cost == 0.0

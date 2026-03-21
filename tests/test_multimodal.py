"""
Tests for multimodal (image_url) content translation in adapters.

Covers:
  - Anthropic: OpenAI image_url → Anthropic image source (base64 + URL)
  - Google: OpenAI image_url → Gemini inlineData/fileData
  - Edge cases: malformed data URIs, mixed content, string passthrough
"""

from proxy.adapters.anthropic import AnthropicAdapter, _translate_content
from proxy.adapters.google import GoogleAdapter, _translate_multimodal_parts


# ══════════════════════════════════════════════════════
# Anthropic Multimodal Translation
# ══════════════════════════════════════════════════════

class TestAnthropicMultimodal:

    def test_string_content_passthrough(self):
        result = _translate_content("Hello world")
        assert result == "Hello world"

    def test_text_part(self):
        result = _translate_content([{"type": "text", "text": "What is this?"}])
        assert result == [{"type": "text", "text": "What is this?"}]

    def test_image_url_http(self):
        result = _translate_content([
            {"type": "image_url", "image_url": {"url": "https://example.com/cat.jpg"}},
        ])
        assert len(result) == 1
        assert result[0]["type"] == "image"
        assert result[0]["source"]["type"] == "url"
        assert result[0]["source"]["url"] == "https://example.com/cat.jpg"

    def test_image_url_base64(self):
        data_uri = "data:image/png;base64,iVBORw0KGgo="
        result = _translate_content([
            {"type": "image_url", "image_url": {"url": data_uri}},
        ])
        assert len(result) == 1
        assert result[0]["type"] == "image"
        assert result[0]["source"]["type"] == "base64"
        assert result[0]["source"]["media_type"] == "image/png"
        assert result[0]["source"]["data"] == "iVBORw0KGgo="

    def test_mixed_text_and_image(self):
        result = _translate_content([
            {"type": "text", "text": "What's in this image?"},
            {"type": "image_url", "image_url": {"url": "https://example.com/photo.jpg"}},
        ])
        assert len(result) == 2
        assert result[0]["type"] == "text"
        assert result[1]["type"] == "image"

    def test_multiple_images(self):
        result = _translate_content([
            {"type": "image_url", "image_url": {"url": "https://example.com/a.jpg"}},
            {"type": "image_url", "image_url": {"url": "https://example.com/b.jpg"}},
        ])
        assert len(result) == 2
        assert all(r["type"] == "image" for r in result)

    def test_malformed_data_uri_fallback(self):
        result = _translate_content([
            {"type": "image_url", "image_url": {"url": "data:badformat"}},
        ])
        assert len(result) == 1
        assert result[0]["type"] == "text"
        assert "[image:" in result[0]["text"]

    def test_unknown_part_type(self):
        result = _translate_content([
            {"type": "audio", "audio_url": {"url": "https://example.com/audio.mp3"}},
        ])
        assert len(result) == 1
        assert result[0]["type"] == "text"

    def test_empty_list_returns_fallback(self):
        result = _translate_content([])
        assert result == [{"type": "text", "text": ""}]

    def test_full_request_with_vision(self):
        """End-to-end: translate_request with multimodal message."""
        adapter = AnthropicAdapter()
        body = {
            "model": "claude-sonnet-4-20250514",
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": "Describe this image"},
                    {"type": "image_url", "image_url": {"url": "https://example.com/cat.jpg"}},
                ],
            }],
        }
        _, translated, _ = adapter.translate_request(
            "https://api.anthropic.com/v1", body, {},
        )
        msg = translated["messages"][0]
        assert isinstance(msg["content"], list)
        assert msg["content"][0]["type"] == "text"
        assert msg["content"][1]["type"] == "image"
        assert msg["content"][1]["source"]["type"] == "url"

    def test_string_message_still_works(self):
        """Non-multimodal messages should still work."""
        adapter = AnthropicAdapter()
        body = {
            "model": "claude-sonnet-4-20250514",
            "messages": [{"role": "user", "content": "Hello"}],
        }
        _, translated, _ = adapter.translate_request(
            "https://api.anthropic.com/v1", body, {},
        )
        assert translated["messages"][0]["content"] == "Hello"


# ══════════════════════════════════════════════════════
# Google Gemini Multimodal Translation
# ══════════════════════════════════════════════════════

class TestGoogleMultimodal:

    def test_text_part(self):
        result = _translate_multimodal_parts([{"type": "text", "text": "Hello"}])
        assert result == [{"text": "Hello"}]

    def test_image_url_base64(self):
        data_uri = "data:image/jpeg;base64,/9j/4AAQ="
        result = _translate_multimodal_parts([
            {"type": "image_url", "image_url": {"url": data_uri}},
        ])
        assert len(result) == 1
        assert "inlineData" in result[0]
        assert result[0]["inlineData"]["mimeType"] == "image/jpeg"
        assert result[0]["inlineData"]["data"] == "/9j/4AAQ="

    def test_image_url_http(self):
        result = _translate_multimodal_parts([
            {"type": "image_url", "image_url": {"url": "https://example.com/photo.png"}},
        ])
        assert len(result) == 1
        assert "fileData" in result[0]
        assert result[0]["fileData"]["mimeType"] == "image/png"
        assert result[0]["fileData"]["fileUri"] == "https://example.com/photo.png"

    def test_mime_type_detection_jpeg(self):
        result = _translate_multimodal_parts([
            {"type": "image_url", "image_url": {"url": "https://example.com/photo.jpg"}},
        ])
        assert result[0]["fileData"]["mimeType"] == "image/jpeg"

    def test_mime_type_detection_gif(self):
        result = _translate_multimodal_parts([
            {"type": "image_url", "image_url": {"url": "https://example.com/anim.gif"}},
        ])
        assert result[0]["fileData"]["mimeType"] == "image/gif"

    def test_mime_type_detection_webp(self):
        result = _translate_multimodal_parts([
            {"type": "image_url", "image_url": {"url": "https://example.com/img.webp"}},
        ])
        assert result[0]["fileData"]["mimeType"] == "image/webp"

    def test_mime_type_default_jpeg(self):
        result = _translate_multimodal_parts([
            {"type": "image_url", "image_url": {"url": "https://example.com/photo"}},
        ])
        assert result[0]["fileData"]["mimeType"] == "image/jpeg"

    def test_mixed_text_and_image(self):
        result = _translate_multimodal_parts([
            {"type": "text", "text": "What's this?"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc123"}},
        ])
        assert len(result) == 2
        assert "text" in result[0]
        assert "inlineData" in result[1]

    def test_malformed_data_uri(self):
        result = _translate_multimodal_parts([
            {"type": "image_url", "image_url": {"url": "data:badformat"}},
        ])
        assert len(result) == 1
        assert "text" in result[0]

    def test_empty_list(self):
        result = _translate_multimodal_parts([])
        assert result == [{"text": ""}]

    def test_full_request_with_vision(self):
        """End-to-end: translate_request with multimodal message."""
        adapter = GoogleAdapter()
        body = {
            "model": "gemini-2.5-flash",
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": "Describe this image"},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,iVBOR="}},
                ],
            }],
        }
        _, translated, _ = adapter.translate_request(
            "https://generativelanguage.googleapis.com/v1beta", body, {},
        )
        parts = translated["contents"][0]["parts"]
        assert len(parts) == 2
        assert "text" in parts[0]
        assert "inlineData" in parts[1]
        assert parts[1]["inlineData"]["mimeType"] == "image/png"

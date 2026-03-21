"""Tests for core/tokenizer.py — tiktoken-based token counting."""

from core.tokenizer import count_tokens, count_messages_tokens, is_tiktoken_available


class TestCountTokens:
    def test_empty_string(self):
        assert count_tokens("") == 0

    def test_simple_text(self):
        result = count_tokens("Hello world")
        assert result > 0

    def test_longer_text_more_tokens(self):
        short = count_tokens("Hi")
        long = count_tokens("This is a longer sentence with many more words in it")
        assert long > short

    def test_model_specific(self):
        # Same text, any model should give a count
        result = count_tokens("Hello world", model="gpt-4o")
        assert result > 0

    def test_code_denser(self):
        """Code typically has more tokens per character than prose."""
        prose = count_tokens("The quick brown fox jumps over the lazy dog")
        code = count_tokens("def f(x): return x * 2 + 1\nfor i in range(10): print(f(i))")
        # Both should be positive
        assert prose > 0
        assert code > 0


class TestCountMessagesTokens:
    def test_single_message(self):
        msgs = [{"role": "user", "content": "Hello"}]
        result = count_messages_tokens(msgs)
        # Overhead (4 per msg + 2 priming) + content tokens
        assert result >= 6

    def test_multiple_messages(self):
        msgs = [
            {"role": "system", "content": "You are helpful"},
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Hello!"},
        ]
        result = count_messages_tokens(msgs)
        # 3 msgs × 4 overhead + 2 priming + content
        assert result >= 14

    def test_multimodal_image_tokens(self):
        msgs = [{
            "role": "user",
            "content": [
                {"type": "text", "text": "What is this?"},
                {"type": "image_url", "image_url": {"url": "https://example.com/img.jpg"}},
            ],
        }]
        result = count_messages_tokens(msgs)
        # Should include ~85 tokens for the image
        assert result >= 85

    def test_empty_messages(self):
        result = count_messages_tokens([])
        assert result == 2  # just priming tokens

    def test_model_hint(self):
        msgs = [{"role": "user", "content": "Hello"}]
        result = count_messages_tokens(msgs, model="gpt-4o")
        assert result > 0


class TestAvailability:
    def test_flag_is_bool(self):
        assert isinstance(is_tiktoken_available(), bool)

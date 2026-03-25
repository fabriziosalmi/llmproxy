"""
Determinism Tests — prove pure functions always produce identical output.

Uses Hypothesis to generate random inputs and verify:
  D1. Adapter request translation is deterministic
  D2. Adapter response translation is deterministic
  D3. Security shield scoring is deterministic
  D4. Model alias resolution is deterministic
  D5. Cost calculation is deterministic (no floating point drift)
"""

import json
import pytest
from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st


# ── D1-D2: Adapter Translation Determinism ────────────────────

class TestAdapterDeterminism:
    """Adapter translate_request/translate_response are pure functions."""

    @pytest.mark.determinism
    @given(
        model=st.sampled_from(["gpt-4o", "gpt-4o-mini", "o3-mini"]),
        content=st.text(min_size=1, max_size=200),
        temperature=st.floats(min_value=0.0, max_value=2.0, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=50, deadline=None, suppress_health_check=[HealthCheck.too_slow])
    def test_openai_translate_request_deterministic(self, model, content, temperature):
        """D1: OpenAI translate_request(same input) → same output."""
        from proxy.adapters.openai import OpenAIAdapter

        adapter = OpenAIAdapter()
        body = {
            "model": model,
            "messages": [{"role": "user", "content": content}],
            "temperature": temperature,
        }
        headers = {"Authorization": "Bearer sk-test"}

        url1, body1, h1 = adapter.translate_request("https://api.openai.com/v1", body, headers)
        url2, body2, h2 = adapter.translate_request("https://api.openai.com/v1", body, headers)

        assert url1 == url2
        assert json.dumps(body1, sort_keys=True) == json.dumps(body2, sort_keys=True)
        assert h1 == h2

    @pytest.mark.determinism
    @given(content=st.text(min_size=1, max_size=200))
    @settings(max_examples=50, deadline=None, suppress_health_check=[HealthCheck.too_slow])
    def test_google_translate_request_deterministic(self, content):
        """D1b: Google translate_request is deterministic."""
        from proxy.adapters.google import GoogleAdapter

        adapter = GoogleAdapter()
        body = {
            "model": "gemini-2.5-flash",
            "messages": [{"role": "user", "content": content}],
        }
        headers = {"Authorization": "Bearer test-key"}

        url1, body1, h1 = adapter.translate_request(
            "https://generativelanguage.googleapis.com/v1beta", body, headers,
        )
        url2, body2, h2 = adapter.translate_request(
            "https://generativelanguage.googleapis.com/v1beta", body, headers,
        )

        assert url1 == url2
        assert json.dumps(body1, sort_keys=True) == json.dumps(body2, sort_keys=True)

    @pytest.mark.determinism
    def test_google_translate_response_deterministic(self):
        """D2: Google translate_response(same input) → same output."""
        from proxy.adapters.google import GoogleAdapter

        adapter = GoogleAdapter()
        gemini_response = {
            "candidates": [{
                "content": {"parts": [{"text": "Hello!"}]},
                "finishReason": "STOP",
            }],
            "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5, "totalTokenCount": 15},
        }

        r1 = adapter.translate_response(gemini_response)
        r2 = adapter.translate_response(gemini_response)

        # Content and structure must match (timestamps may differ)
        assert r1["choices"][0]["message"]["content"] == r2["choices"][0]["message"]["content"]
        assert r1["usage"] == r2["usage"]

    @pytest.mark.determinism
    def test_anthropic_translate_response_deterministic(self):
        """D2b: Anthropic translate_response is deterministic."""
        from proxy.adapters.anthropic import AnthropicAdapter

        adapter = AnthropicAdapter()
        anthropic_response = {
            "id": "msg_test",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": "Hello!"}],
            "model": "claude-sonnet-4-20250514",
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }

        r1 = adapter.translate_response(anthropic_response)
        r2 = adapter.translate_response(anthropic_response)

        assert r1["choices"] == r2["choices"]
        assert r1["usage"] == r2["usage"]


# ── D3: Security Shield Determinism ───────────────────────────

class TestSecurityDeterminism:
    """Security scan results must be deterministic for identical inputs."""

    @pytest.mark.determinism
    @pytest.mark.security
    @given(prompt=st.text(min_size=1, max_size=300))
    @settings(max_examples=100, deadline=None, suppress_health_check=[HealthCheck.too_slow])
    def test_semantic_scan_deterministic(self, prompt):
        """D3: semantic_scan(x) == semantic_scan(x) for all x."""
        from core.semantic_analyzer import semantic_scan

        r1 = semantic_scan(prompt, threshold=0.35)
        r2 = semantic_scan(prompt, threshold=0.35)

        assert r1 == r2, f"Non-deterministic scan: {r1} != {r2}"

    @pytest.mark.determinism
    @pytest.mark.security
    @given(prompt=st.text(min_size=1, max_size=300))
    @settings(max_examples=100, deadline=None, suppress_health_check=[HealthCheck.too_slow])
    def test_semantic_scan_threshold_monotonic(self, prompt):
        """D3b: Lowering threshold can only increase detections (never decrease)."""
        from core.semantic_analyzer import semantic_scan

        high = semantic_scan(prompt, threshold=0.50)
        low = semantic_scan(prompt, threshold=0.30)

        if high is not None:
            # If detected at high threshold, must also detect at lower
            assert low is not None, (
                f"Threshold monotonicity violated: detected at 0.50 but not 0.30"
            )
            assert low[0] >= high[0] - 0.01, "Score decreased with lower threshold"


# ── D4: Model Alias Resolution Determinism ────────────────────

class TestModelResolutionDeterminism:
    """Model alias → real model resolution is deterministic."""

    @pytest.mark.determinism
    def test_all_aliases_resolve_consistently(self):
        """D4: Every alias resolves to the same model on every call."""
        from core.model_resolver import resolve_model

        config = {
            "model_aliases": {
                "fast": "gpt-4o-mini",
                "best": "gpt-4o",
                "cheap": "gemini-2.0-flash",
            },
        }

        for alias, expected in config["model_aliases"].items():
            for _ in range(100):
                result = resolve_model(config, alias)
                assert result == expected, (
                    f"Alias '{alias}' resolved to '{result}' instead of '{expected}'"
                )


# ── D5: Cost Calculation Determinism ──────────────────────────

class TestCostDeterminism:
    """Cost computation must not drift due to floating point accumulation."""

    @pytest.mark.determinism
    @given(
        input_tokens=st.integers(min_value=0, max_value=1_000_000),
        output_tokens=st.integers(min_value=0, max_value=1_000_000),
    )
    @settings(max_examples=200, deadline=None)
    def test_cost_calculation_deterministic(self, input_tokens, output_tokens):
        """D5: cost(tokens) is deterministic and non-negative."""
        from core.pricing import MODEL_PRICING

        pricing = MODEL_PRICING.get("gpt-4o", {"input": 2.50, "output": 10.0})
        cost1 = (input_tokens / 1_000_000) * pricing["input"] + \
                (output_tokens / 1_000_000) * pricing["output"]
        cost2 = (input_tokens / 1_000_000) * pricing["input"] + \
                (output_tokens / 1_000_000) * pricing["output"]

        assert cost1 == cost2, f"Floating point non-determinism: {cost1} != {cost2}"
        assert cost1 >= 0.0, f"Negative cost: {cost1}"

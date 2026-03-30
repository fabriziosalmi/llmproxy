"""Tests for core.pricing — per-model cost estimation."""

from core.pricing import (
    estimate_cost,
    estimate_cost_pre_flight,
    get_pricing,
    set_config_overrides,
    MODEL_PRICING,
    _config_overrides,
)


class TestGetPricing:
    def test_known_model(self):
        p = get_pricing("gpt-4o")
        assert p["input"] == 2.50
        assert p["output"] == 10.00

    def test_anthropic_model(self):
        p = get_pricing("claude-sonnet-4-20250514")
        assert p["input"] == 3.00
        assert p["output"] == 15.00

    def test_local_model_free(self):
        p = get_pricing("llama3.3")
        assert p["input"] == 0.0
        assert p["output"] == 0.0

    def test_unknown_model_returns_default(self):
        p = get_pricing("some-unknown-model-xyz")
        assert p["input"] == 1.00
        assert p["output"] == 3.00

    def test_prefix_matching(self):
        """Versioned model names should match their base model."""
        p = get_pricing("gpt-4o-2024-08-06")
        assert p["input"] == 2.50  # matches "gpt-4o"

    def test_config_override(self):
        """Config overrides take priority over static table."""
        _config_overrides.clear()
        set_config_overrides({"custom-model": {"input": 0.01, "output": 0.02}})
        p = get_pricing("custom-model")
        assert p["input"] == 0.01
        assert p["output"] == 0.02
        _config_overrides.clear()

    def test_config_override_trumps_static(self):
        _config_overrides.clear()
        set_config_overrides({"gpt-4o": {"input": 99.0, "output": 99.0}})
        p = get_pricing("gpt-4o")
        assert p["input"] == 99.0
        _config_overrides.clear()


class TestEstimateCost:
    def test_gpt4o_cost(self):
        # 1M input + 1M output at $2.50/$10.00
        cost = estimate_cost("gpt-4o", 1_000_000, 1_000_000)
        assert cost == 12.50

    def test_claude_haiku_much_cheaper(self):
        cost_haiku = estimate_cost("claude-haiku-4-5-20251001", 1000, 1000)
        cost_opus = estimate_cost("claude-opus-4-20250514", 1000, 1000)
        # Opus should be ~6x more expensive than Haiku
        assert cost_opus > cost_haiku * 5

    def test_groq_extremely_cheap(self):
        cost_groq = estimate_cost("llama-3.3-70b-versatile", 1_000_000, 1_000_000)
        cost_gpt4o = estimate_cost("gpt-4o", 1_000_000, 1_000_000)
        # Groq should be >80x cheaper than GPT-4o
        assert cost_gpt4o / cost_groq > 80

    def test_local_model_zero_cost(self):
        cost = estimate_cost("llama3.3", 1_000_000, 1_000_000)
        assert cost == 0.0

    def test_deepseek_cheap(self):
        cost = estimate_cost("deepseek-chat", 1_000_000, 1_000_000)
        # DeepSeek V3.2: $0.28 in + $0.42 out = $0.70 per 1M each
        assert abs(cost - 0.70) < 0.01

    def test_zero_tokens(self):
        cost = estimate_cost("gpt-4o", 0, 0)
        assert cost == 0.0

    def test_small_request(self):
        # 100 tokens in, 50 out with gpt-4o-mini ($0.15/$0.60)
        cost = estimate_cost("gpt-4o-mini", 100, 50)
        expected = (100 / 1_000_000) * 0.15 + (50 / 1_000_000) * 0.60
        assert abs(cost - expected) < 1e-10


class TestEstimateCostPreFlight:
    def test_pre_flight_with_ratio(self):
        # 1000 input tokens, 0.5 ratio → 500 estimated output
        cost = estimate_cost_pre_flight("gpt-4o", 1000, avg_output_ratio=0.5)
        expected = (1000 / 1_000_000) * 2.50 + (500 / 1_000_000) * 10.00
        assert abs(cost - expected) < 1e-10

    def test_pre_flight_custom_ratio(self):
        cost_low = estimate_cost_pre_flight("gpt-4o", 1000, avg_output_ratio=0.1)
        cost_high = estimate_cost_pre_flight("gpt-4o", 1000, avg_output_ratio=2.0)
        assert cost_high > cost_low


class TestPricingCompleteness:
    def test_all_config_models_have_pricing(self):
        """Key models from config.yaml should have pricing entries."""
        critical_models = [
            "gpt-4o", "gpt-4o-mini", "gpt-4.1",
            "claude-sonnet-4-20250514", "claude-haiku-4-5-20251001",
            "gemini-2.5-pro", "gemini-2.5-flash",
            "deepseek-chat", "mistral-large-latest",
        ]
        for model in critical_models:
            p = get_pricing(model)
            assert "input" in p, f"Missing pricing for {model}"
            assert "output" in p, f"Missing pricing for {model}"

    def test_pricing_table_has_minimum_models(self):
        assert len(MODEL_PRICING) >= 25

    def test_all_prices_non_negative(self):
        for model, prices in MODEL_PRICING.items():
            assert prices["input"] >= 0, f"Negative input price for {model}"
            assert prices["output"] >= 0, f"Negative output price for {model}"

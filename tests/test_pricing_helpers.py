"""Tier A.2 + A.3 — pricing helpers.

A.2: get_pricing() emits a one-shot WARNING when a model name has no
     entry in MODEL_PRICING and falls back to _DEFAULT_PRICING. Without
     this signal, operators silently absorb $1/$3 estimates for every
     custom-fine-tuned or new-model name.

A.3: estimate_baseline_savings() returns a model-mix economics summary
     vs the most-expensive paid model in MODEL_PRICING. Used by
     /api/v1/analytics/cost-efficiency to answer "is the multi-provider
     routing actually saving me money?"
"""

import logging

import pytest

from core import pricing
from core.pricing import (
    MODEL_PRICING,
    _DEFAULT_PRICING,
    baseline_premium_pricing,
    estimate_baseline_savings,
    get_pricing,
)


# ── A.2: default-pricing fallback warning ─────────────────────────


@pytest.fixture(autouse=True)
def _reset_warned_set():
    """Each test starts with a clean warned-set so the one-shot semantics
    are observable per test, not coupled to test execution order."""
    pricing._DEFAULT_PRICING_WARNED.clear()
    yield
    pricing._DEFAULT_PRICING_WARNED.clear()


class TestDefaultPricingFallbackWarning:
    def test_known_model_no_warning(self, caplog):
        """Models in MODEL_PRICING resolve cleanly with no log noise."""
        with caplog.at_level(logging.WARNING, logger="llmproxy.pricing"):
            result = get_pricing("gpt-4o")
        assert result == MODEL_PRICING["gpt-4o"]
        assert not [r for r in caplog.records if "default pricing" in r.message]

    def test_prefix_match_no_warning(self, caplog):
        """Versioned model names hit the prefix-matching path — must not
        warn. Catches a regression where the warning fires before the
        prefix lookup."""
        with caplog.at_level(logging.WARNING, logger="llmproxy.pricing"):
            # `gpt-4o-2024-08-06` should prefix-match gpt-4o
            result = get_pricing("gpt-4o-2024-08-06")
        assert result == MODEL_PRICING["gpt-4o"]
        assert not [r for r in caplog.records if "default pricing" in r.message]

    def test_unknown_model_warns_once(self, caplog):
        """First lookup for an unknown name emits a WARNING; the result
        is _DEFAULT_PRICING."""
        with caplog.at_level(logging.WARNING, logger="llmproxy.pricing"):
            result = get_pricing("totally-fake-model-9999")
        assert result == _DEFAULT_PRICING
        warns = [r for r in caplog.records if "default pricing" in r.message]
        assert len(warns) == 1
        assert "totally-fake-model-9999" in warns[0].message

    def test_unknown_model_warning_is_one_shot(self, caplog):
        """Same name looked up 101 times → still exactly 1 warning.
        Without this guard the log would flood at request rate."""
        with caplog.at_level(logging.WARNING, logger="llmproxy.pricing"):
            for _ in range(101):
                get_pricing("repeat-fake-model")
        warns = [r for r in caplog.records if "default pricing" in r.message]
        assert len(warns) == 1

    def test_distinct_unknown_models_each_warn_once(self, caplog):
        """Each unique unknown name warns independently — operators
        running multiple custom models all get told."""
        with caplog.at_level(logging.WARNING, logger="llmproxy.pricing"):
            get_pricing("custom-a")
            get_pricing("custom-b")
            get_pricing("custom-c")
            get_pricing("custom-a")  # repeat — no extra warning
        warns = [r for r in caplog.records if "default pricing" in r.message]
        assert len(warns) == 3
        assert {w.message for w in warns} != {warns[0].message}  # distinct

    def test_empty_model_string_does_not_warn(self, caplog):
        """An empty model name shouldn't warn — that's a caller bug, not
        an unknown-pricing issue, and would flood logs from old clients
        that omit the model field."""
        with caplog.at_level(logging.WARNING, logger="llmproxy.pricing"):
            result = get_pricing("")
        assert result == _DEFAULT_PRICING
        warns = [r for r in caplog.records if "default pricing" in r.message]
        assert len(warns) == 0


# ── A.3: baseline savings estimation ──────────────────────────────


class TestBaselinePremiumPricing:
    def test_returns_max_paid_rates(self):
        """Excludes free models — they'd give a misleading $0 baseline."""
        baseline = baseline_premium_pricing()
        max_input = max(p["input"] for p in MODEL_PRICING.values() if p["input"] > 0)
        max_output = max(p["output"] for p in MODEL_PRICING.values() if p["output"] > 0)
        assert baseline["input"] == max_input
        assert baseline["output"] == max_output

    def test_input_at_least_1_dollar_per_mtok(self):
        """Sanity: the premium baseline should be ≥ $1/MTok input. If
        this trips, MODEL_PRICING got crushed during a refactor."""
        assert baseline_premium_pricing()["input"] >= 1.0


class TestEstimateBaselineSavings:
    def test_empty_rows_returns_zeros(self):
        savings = estimate_baseline_savings([])
        assert savings["baseline_usd"] == 0.0
        assert savings["actual_usd"] == 0.0
        assert savings["saved_usd"] == 0.0
        assert savings["saved_pct"] == 0.0

    def test_savings_against_premium_baseline(self):
        """200k input + 100k output tokens at $0.15/$0.60 (gpt-4o-mini)
        actual = $0.03 + $0.06 = $0.09. Premium baseline at e.g. o3
        ($10/$40) = $2.00 + $4.00 = $6.00. Saved = $5.91."""
        rows = [
            {
                "model": "gpt-4o-mini",
                "total_prompt_tokens": 200_000,
                "total_completion_tokens": 100_000,
                "total_cost_usd": 0.09,
            },
        ]
        savings = estimate_baseline_savings(rows)
        # Actual matches the row.
        assert savings["actual_usd"] == pytest.approx(0.09)
        # Baseline is computed from premium pricing.
        baseline = baseline_premium_pricing()
        expected_baseline = (200_000 / 1e6) * baseline["input"] + (
            100_000 / 1e6
        ) * baseline["output"]
        assert savings["baseline_usd"] == pytest.approx(round(expected_baseline, 4))
        # Saved is positive and sensible.
        assert savings["saved_usd"] > 0
        # Percent is in [0, 100].
        assert 0 < savings["saved_pct"] <= 100

    def test_saved_clamps_at_zero_when_actual_exceeds_baseline(self):
        """If the user actually picked the premium tier (or worse —
        e.g. fine-tuned costing > base), saved must NOT go negative.
        We don't lie with negative numbers; saved=0 means 'no win to
        report'."""
        rows = [
            {
                "model": "expensive-finetune",
                "total_prompt_tokens": 1_000_000,
                "total_completion_tokens": 1_000_000,
                "total_cost_usd": 100_000.0,  # absurdly higher than baseline
            },
        ]
        savings = estimate_baseline_savings(rows)
        assert savings["saved_usd"] == 0.0
        assert savings["saved_pct"] == 0.0

    def test_handles_missing_token_fields_gracefully(self):
        """SQLite SUM() returns None for empty result sets. The helper
        must treat None as 0, not raise TypeError."""
        rows = [
            {
                "model": "x",
                "total_prompt_tokens": None,
                "total_completion_tokens": None,
                "total_cost_usd": None,
            },
        ]
        savings = estimate_baseline_savings(rows)
        assert savings["baseline_usd"] == 0.0
        assert savings["actual_usd"] == 0.0
        assert savings["saved_usd"] == 0.0

    def test_aggregates_across_multiple_models(self):
        """Per-model rows roll up into a single summary."""
        rows = [
            {
                "model": "gpt-4o-mini",
                "total_prompt_tokens": 100_000,
                "total_completion_tokens": 50_000,
                "total_cost_usd": 0.045,
            },
            {
                "model": "ollama/llama3.3",
                "total_prompt_tokens": 500_000,
                "total_completion_tokens": 500_000,
                "total_cost_usd": 0.0,
            },
        ]
        savings = estimate_baseline_savings(rows)
        assert savings["actual_usd"] == pytest.approx(0.045)
        # Free-model tokens contribute fully to the baseline (would have
        # cost something on the premium tier) — that's the whole point
        # of the metric: "you'd have paid $X if you didn't route to free."
        assert savings["baseline_usd"] > 0
        assert savings["saved_usd"] > 0

    def test_baseline_rates_are_returned(self):
        """The response includes the baseline rates so the UI can show
        'baseline = $X/MTok in / $Y/MTok out'."""
        rows = [
            {
                "model": "x",
                "total_prompt_tokens": 1000,
                "total_completion_tokens": 1000,
                "total_cost_usd": 0.0,
            }
        ]
        savings = estimate_baseline_savings(rows)
        assert "baseline_input_per_mtok" in savings
        assert "baseline_output_per_mtok" in savings
        assert savings["baseline_input_per_mtok"] > 0
        assert savings["baseline_output_per_mtok"] > 0

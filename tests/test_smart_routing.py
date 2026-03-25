"""
Tests for smart weighted routing in neural_router.py.

Covers:
  - EMA stats tracking
  - Score computation
  - Strategy selection (smart weighted vs round-robin cold start)
  - Stats update from rotator pipeline
"""

import asyncio
import pytest

from plugins.default.neural_router import (
    update_endpoint_stats,
    get_endpoint_stats,
    _compute_score,
    _endpoint_stats,
)


class TestEndpointStats:
    def setup_method(self):
        _endpoint_stats.clear()

    @pytest.mark.asyncio
    async def test_first_update_sets_values(self):
        await update_endpoint_stats("ep1", 100.0, True)
        stats = get_endpoint_stats("ep1")
        assert stats["latency_ms"] == 100.0
        assert stats["success_rate"] == 1.0
        assert stats["request_count"] == 1

    @pytest.mark.asyncio
    async def test_ema_smoothing(self):
        await update_endpoint_stats("ep1", 100.0, True)
        await update_endpoint_stats("ep1", 200.0, True)
        stats = get_endpoint_stats("ep1")
        # EMA with alpha=0.2: 0.2*200 + 0.8*100 = 120
        assert abs(stats["latency_ms"] - 120.0) < 0.01

    @pytest.mark.asyncio
    async def test_failure_reduces_success_rate(self):
        await update_endpoint_stats("ep1", 100.0, True)
        await update_endpoint_stats("ep1", 100.0, False)
        stats = get_endpoint_stats("ep1")
        # EMA: 0.2*0 + 0.8*1.0 = 0.8
        assert abs(stats["success_rate"] - 0.8) < 0.01

    @pytest.mark.asyncio
    async def test_request_count_increments(self):
        await update_endpoint_stats("ep1", 50.0, True)
        await update_endpoint_stats("ep1", 50.0, True)
        await update_endpoint_stats("ep1", 50.0, True)
        assert get_endpoint_stats("ep1")["request_count"] == 3

    def test_unknown_endpoint_returns_defaults(self):
        stats = get_endpoint_stats("nonexistent")
        assert stats["latency_ms"] == 0.0
        assert stats["success_rate"] == 1.0
        assert stats["request_count"] == 0

    @pytest.mark.asyncio
    async def test_multiple_endpoints_isolated(self):
        await update_endpoint_stats("fast", 50.0, True)
        await update_endpoint_stats("slow", 500.0, True)
        assert get_endpoint_stats("fast")["latency_ms"] == 50.0
        assert get_endpoint_stats("slow")["latency_ms"] == 500.0


class TestScoreComputation:
    def test_fast_reliable_scores_high(self):
        score = _compute_score(None, {"latency_ms": 50.0, "success_rate": 1.0})
        assert score > 0

    def test_slow_scores_lower(self):
        fast = _compute_score(None, {"latency_ms": 50.0, "success_rate": 1.0})
        slow = _compute_score(None, {"latency_ms": 500.0, "success_rate": 1.0})
        assert fast > slow

    def test_unreliable_scores_lower(self):
        reliable = _compute_score(None, {"latency_ms": 100.0, "success_rate": 1.0})
        unreliable = _compute_score(None, {"latency_ms": 100.0, "success_rate": 0.5})
        assert reliable > unreliable

    def test_success_rate_squared_penalty(self):
        # 50% success should score 4x lower than 100% at same latency
        full = _compute_score(None, {"latency_ms": 100.0, "success_rate": 1.0})
        half = _compute_score(None, {"latency_ms": 100.0, "success_rate": 0.5})
        assert abs(full / half - 4.0) < 0.01

    def test_zero_latency_uses_minimum(self):
        # Should not crash with zero latency
        score = _compute_score(None, {"latency_ms": 0.0, "success_rate": 1.0})
        assert score > 0

    def test_default_values(self):
        # Unknown endpoint with defaults
        score = _compute_score(None, {})
        assert score > 0

    def test_groq_vs_openai_realistic(self):
        """Groq (fast, cheap) should score higher than OpenAI (slower) at equal success."""
        groq = _compute_score(None, {"latency_ms": 80.0, "success_rate": 0.99})
        openai = _compute_score(None, {"latency_ms": 400.0, "success_rate": 0.99})
        assert groq > openai

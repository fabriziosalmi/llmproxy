"""
Tests for cost-aware routing: neural router scoring + budget downgrade.
"""

import pytest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from plugins.default.neural_router import (
    _compute_score, update_endpoint_stats, get_endpoint_stats, _endpoint_stats,
)


# ══════════════════════════════════════════════════════════
# _compute_score with cost awareness
# ══════════════════════════════════════════════════════════

class TestCostAwareScoring:

    def setup_method(self):
        """Clear endpoint stats between tests."""
        _endpoint_stats.clear()

    def test_base_score_without_cost(self):
        """With cost_weight=0, score is pure performance (success²/latency)."""
        stats = {"success_rate": 1.0, "latency_ms": 100.0}
        score = _compute_score(None, stats, model="", cost_weight=0.0)
        assert score == pytest.approx(1.0 / 100.0)

    def test_base_score_without_model(self):
        """Without model, cost factor is ignored even if cost_weight > 0."""
        stats = {"success_rate": 1.0, "latency_ms": 100.0}
        score_no_model = _compute_score(None, stats, model="", cost_weight=0.5)
        score_with_model = _compute_score(None, stats, model="gpt-4o", cost_weight=0.5)
        # No model → pure base score; with model → cost-adjusted
        assert score_no_model == pytest.approx(1.0 / 100.0)
        assert score_with_model != score_no_model

    def test_cheaper_model_scores_higher(self):
        """With same latency/success, cheaper model gets higher score."""
        stats = {"success_rate": 1.0, "latency_ms": 100.0}

        score_cheap = _compute_score(None, stats, model="gpt-4o-mini", cost_weight=0.5)
        score_expensive = _compute_score(None, stats, model="gpt-4o", cost_weight=0.5)

        # gpt-4o-mini ($0.15/MTok) should score higher than gpt-4o ($2.50/MTok)
        assert score_cheap > score_expensive

    def test_free_model_scores_highest(self):
        """Local/free models ($0) score highest on cost factor."""
        stats = {"success_rate": 1.0, "latency_ms": 100.0}

        score_free = _compute_score(None, stats, model="ollama/llama3.3", cost_weight=0.5)
        score_cheap = _compute_score(None, stats, model="gpt-4o-mini", cost_weight=0.5)

        assert score_free > score_cheap

    def test_cost_weight_zero_ignores_cost(self):
        """cost_weight=0 makes cheap and expensive models score the same."""
        stats = {"success_rate": 1.0, "latency_ms": 100.0}

        score_cheap = _compute_score(None, stats, model="gpt-4o-mini", cost_weight=0.0)
        score_expensive = _compute_score(None, stats, model="gpt-4o", cost_weight=0.0)

        assert score_cheap == score_expensive

    def test_high_cost_weight_strongly_favors_cheap(self):
        """cost_weight=1.0 creates a large gap between cheap and expensive."""
        stats = {"success_rate": 1.0, "latency_ms": 100.0}

        score_cheap_low = _compute_score(None, stats, model="gpt-4o-mini", cost_weight=0.1)
        score_cheap_high = _compute_score(None, stats, model="gpt-4o-mini", cost_weight=1.0)
        score_expensive_high = _compute_score(None, stats, model="gpt-4o", cost_weight=1.0)

        # Higher cost_weight → larger gap
        ratio_low = score_cheap_low / _compute_score(None, stats, model="gpt-4o", cost_weight=0.1)
        ratio_high = score_cheap_high / score_expensive_high
        assert ratio_high > ratio_low

    def test_success_rate_still_dominates(self):
        """An expensive but reliable endpoint beats a cheap but very flaky one."""
        reliable_stats = {"success_rate": 0.99, "latency_ms": 200.0}
        flaky_stats = {"success_rate": 0.30, "latency_ms": 100.0}

        score_reliable = _compute_score(None, reliable_stats, model="gpt-4o", cost_weight=0.3)
        score_flaky = _compute_score(None, flaky_stats, model="gpt-4o-mini", cost_weight=0.3)

        # Reliable should still win despite being more expensive (success²=0.98 vs 0.09)
        assert score_reliable > score_flaky

    def test_latency_still_matters(self):
        """A cheaper but very slow endpoint loses to a pricier but fast one."""
        slow_stats = {"success_rate": 1.0, "latency_ms": 5000.0}
        fast_stats = {"success_rate": 1.0, "latency_ms": 50.0}

        score_slow_cheap = _compute_score(None, slow_stats, model="gpt-4o-mini", cost_weight=0.3)
        score_fast_expensive = _compute_score(None, fast_stats, model="gpt-4o", cost_weight=0.3)

        # 100x latency difference should outweigh ~17x price difference
        assert score_fast_expensive > score_slow_cheap


# ══════════════════════════════════════════════════════════
# Budget downgrade to local
# ══════════════════════════════════════════════════════════

class TestBudgetDowngrade:

    @pytest.mark.asyncio
    async def test_no_downgrade_under_limit(self):
        """When under budget, model is NOT downgraded."""
        from tests.test_pipeline_e2e import PipelineAgent
        import httpx

        agent = PipelineAgent(config={
            "server": {"auth": {"enabled": False}},
            "caching": {"enabled": False, "negative_cache": {"maxsize": 100, "ttl": 60}},
            "plugins": {},
            "budget": {
                "daily_limit": 50.0,
                "soft_limit": 40.0,
                "fallback_to_local_on_limit": True,
                "local_model": "ollama/llama3.3",
            },
        })
        agent.total_cost_today = 10.0  # well under limit

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=agent.app),
            base_url="http://test",
        ) as client:
            resp = await client.post("/v1/chat/completions", json={
                "model": "gpt-4o", "messages": [{"role": "user", "content": "Hi"}],
            })

        assert resp.status_code == 200
        # Model should NOT have been downgraded
        assert "_budget_downgrade" not in agent.forwarder.forward_with_fallback.call_args

    @pytest.mark.asyncio
    async def test_downgrade_when_over_limit(self):
        """When over budget limit, model is downgraded to local."""
        from tests.test_pipeline_e2e import PipelineAgent
        import httpx

        agent = PipelineAgent(config={
            "server": {"auth": {"enabled": False}},
            "caching": {"enabled": False, "negative_cache": {"maxsize": 100, "ttl": 60}},
            "plugins": {},
            "budget": {
                "daily_limit": 50.0,
                "soft_limit": 40.0,
                "fallback_to_local_on_limit": True,
                "local_model": "ollama/llama3.3",
            },
        })
        agent.total_cost_today = 55.0  # over limit

        # Track what model was sent to forwarder
        forwarded_model = None
        original_forward = agent._mock_forward

        async def capture_forward(ctx, target, headers, session):
            nonlocal forwarded_model
            forwarded_model = ctx.body.get("model")
            await original_forward(ctx, target, headers, session)

        agent.forwarder.forward_with_fallback.side_effect = capture_forward

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=agent.app),
            base_url="http://test",
        ) as client:
            resp = await client.post("/v1/chat/completions", json={
                "model": "gpt-4o", "messages": [{"role": "user", "content": "Hi"}],
            })

        assert resp.status_code == 200
        # Model should have been downgraded to local
        assert forwarded_model == "ollama/llama3.3"

    @pytest.mark.asyncio
    async def test_no_downgrade_without_config(self):
        """Without fallback_to_local_on_limit, no downgrade even over limit."""
        from tests.test_pipeline_e2e import PipelineAgent
        import httpx

        agent = PipelineAgent(config={
            "server": {"auth": {"enabled": False}},
            "caching": {"enabled": False, "negative_cache": {"maxsize": 100, "ttl": 60}},
            "plugins": {},
            "budget": {
                "daily_limit": 50.0,
                # fallback_to_local_on_limit NOT set
            },
        })
        agent.total_cost_today = 55.0

        forwarded_model = None
        original_forward = agent._mock_forward

        async def capture_forward(ctx, target, headers, session):
            nonlocal forwarded_model
            forwarded_model = ctx.body.get("model")
            await original_forward(ctx, target, headers, session)

        agent.forwarder.forward_with_fallback.side_effect = capture_forward

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=agent.app),
            base_url="http://test",
        ) as client:
            resp = await client.post("/v1/chat/completions", json={
                "model": "gpt-4o", "messages": [{"role": "user", "content": "Hi"}],
            })

        assert resp.status_code == 200
        assert forwarded_model == "gpt-4o"  # NOT downgraded

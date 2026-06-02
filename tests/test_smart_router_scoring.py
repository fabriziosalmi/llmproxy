"""Tier A.1 — Pin the routing scoring formula.

The cost-routing slider has been in production for a while, but only the
admin endpoint was tested — never the formula it controls. These tests
pin the *ranking behavior* across realistic endpoint sets so a future
refactor of `_compute_score` can't silently change which endpoint wins.

Formula under test (plugins/default/smart_router._compute_score):

    base_score  = success_rate^2 / max(latency_ms, 1)
    cost_factor = 1.0 / (input_price_per_mtok + 0.01)
    final       = base_score * (cost_factor ** cost_weight)

The tests assert *ranking*, not absolute numbers — small numerical drift
in the formula is fine; flipping the winner is a bug.
"""

from types import SimpleNamespace


from plugins.default.smart_router import _compute_score


# ── Fixtures: a few stand-in endpoints + their stats ──────────────


def _ep(eid: str) -> SimpleNamespace:
    """A skeletal endpoint object — only `id` is consulted by the score."""
    return SimpleNamespace(id=eid)


# Realistic stats: gpt-4o-mini is fast+reliable, claude-opus is slow+reliable,
# llama3.3 is fast+local, sonar-pro is moderate+expensive.
_STATS_FAST_RELIABLE = {"latency_ms": 200.0, "success_rate": 1.0}
_STATS_SLOW_RELIABLE = {"latency_ms": 1500.0, "success_rate": 1.0}
_STATS_FAST_FLAKY = {"latency_ms": 200.0, "success_rate": 0.7}
_STATS_DEFAULT = {"latency_ms": 500.0, "success_rate": 1.0}


# ── cost_weight = 0.0 (ignore cost) ───────────────────────────────


class TestCostWeightZero:
    """At cw=0.0 the formula collapses to pure performance: success²/latency.
    Cost should be utterly irrelevant — a free local model with bad
    latency must lose to a fast cloud model."""

    def test_fast_wins_over_slow_regardless_of_price(self):
        # Fast expensive vs slow free — fast should win when ignoring cost.
        fast_expensive = _compute_score(
            _ep("gpt-4o"),
            _STATS_FAST_RELIABLE,
            model="gpt-4o",
            cost_weight=0.0,
        )
        slow_free = _compute_score(
            _ep("ollama"),
            _STATS_SLOW_RELIABLE,
            model="ollama/llama3.3",
            cost_weight=0.0,
        )
        assert fast_expensive > slow_free

    def test_model_argument_ignored_when_weight_zero(self):
        """Passing a model name shouldn't change the score at cw=0 — pricing
        is short-circuited. Catches accidental pricing leakage."""
        with_model = _compute_score(
            _ep("e1"),
            _STATS_DEFAULT,
            model="gpt-4o",
            cost_weight=0.0,
        )
        without_model = _compute_score(
            _ep("e1"),
            _STATS_DEFAULT,
            model="",
            cost_weight=0.0,
        )
        assert with_model == without_model


# ── cost_weight > 0 ranking behavior ──────────────────────────────


class TestCostWeightModerate:
    """Default cost_weight = 0.3. With equal performance, cheaper should win
    by a measurable but not dominating margin."""

    def test_cheaper_wins_when_perf_equal(self):
        cheap = _compute_score(
            _ep("haiku"),
            _STATS_FAST_RELIABLE,
            model="claude-haiku-4-5-20251001",
            cost_weight=0.3,
        )  # input=0.80
        expensive = _compute_score(
            _ep("opus"),
            _STATS_FAST_RELIABLE,
            model="claude-opus-4-6",
            cost_weight=0.3,
        )  # input=5.00
        assert cheap > expensive

    def test_free_model_dominates_at_equal_perf(self):
        """A free local model (input_price=0) gets cost_factor=100 → big
        boost. With equal performance it should crush a paid model."""
        free = _compute_score(
            _ep("local"),
            _STATS_FAST_RELIABLE,
            model="ollama/llama3.3",
            cost_weight=0.3,
        )
        paid = _compute_score(
            _ep("openai"),
            _STATS_FAST_RELIABLE,
            model="gpt-4o",
            cost_weight=0.3,
        )
        assert free > paid
        # Sanity: it's a meaningful boost, not just a tiebreaker.
        assert free / paid > 2.0

    def test_great_perf_beats_cheaper_with_terrible_perf(self):
        """The slider is moderate (0.3) — performance still matters more
        than price in extreme cases. A 7x latency gap should overpower
        a 5x price advantage."""
        slow_cheap = _compute_score(
            _ep("haiku"),
            {"latency_ms": 1500.0, "success_rate": 1.0},
            model="claude-haiku-4-5-20251001",
            cost_weight=0.3,
        )
        fast_expensive = _compute_score(
            _ep("opus"),
            {"latency_ms": 200.0, "success_rate": 1.0},
            model="claude-opus-4-6",
            cost_weight=0.3,
        )
        assert fast_expensive > slow_cheap


class TestCostWeightFull:
    """cost_weight = 1.0: cost dominates. Cheap-but-slow should beat
    fast-but-expensive in this regime — the operator is saying 'price me
    out of the cloud'."""

    def test_cheap_slow_beats_fast_expensive_at_full_weight(self):
        slow_free = _compute_score(
            _ep("local"),
            {"latency_ms": 1500.0, "success_rate": 1.0},
            model="ollama/llama3.3",
            cost_weight=1.0,
        )
        fast_expensive = _compute_score(
            _ep("openai"),
            {"latency_ms": 200.0, "success_rate": 1.0},
            model="gpt-4o",
            cost_weight=1.0,
        )
        assert slow_free > fast_expensive


# ── Reliability: success_rate squared ─────────────────────────────


class TestSuccessRatePenalty:
    """success² in the numerator — a flaky endpoint loses score quadratically.
    Pin this so a future change to linear-in-success doesn't sneak in."""

    def test_flaky_endpoint_scored_below_reliable(self):
        reliable = _compute_score(
            _ep("a"),
            _STATS_FAST_RELIABLE,
            model="gpt-4o-mini",
            cost_weight=0.3,
        )
        flaky = _compute_score(
            _ep("b"),
            _STATS_FAST_FLAKY,
            model="gpt-4o-mini",
            cost_weight=0.3,
        )
        assert reliable > flaky
        # Quadratic penalty: 0.7² = 0.49 → flaky should lose by ~half,
        # not by 30%. Tightens the contract.
        assert reliable / flaky > 1.8


# ── Edge cases ────────────────────────────────────────────────────


class TestEdgeCases:
    """Defensive numerical behavior — none of these should crash or
    silently produce nonsense."""

    def test_zero_latency_uses_min_floor(self):
        """Cold-start endpoints can report 0 latency. The formula must
        clamp to 1 ms to avoid division-by-zero — score should be finite."""
        score = _compute_score(
            _ep("cold"),
            {"latency_ms": 0.0, "success_rate": 1.0},
            model="gpt-4o",
            cost_weight=0.3,
        )
        assert score > 0
        assert score < float("inf")

    def test_unknown_model_uses_default_pricing(self):
        """Unknown model name → core.pricing returns _DEFAULT_PRICING
        ($1/$3). Score must still compute (no KeyError, no NaN)."""
        score = _compute_score(
            _ep("unknown"),
            _STATS_DEFAULT,
            model="not-a-real-model-12345",
            cost_weight=0.3,
        )
        assert score > 0
        assert score == score  # not NaN

    def test_empty_model_skips_cost_calculation(self):
        """Code path: `if not model: return base_score`. Should not look
        up pricing or apply the cost factor."""
        with_empty = _compute_score(
            _ep("e1"),
            _STATS_DEFAULT,
            model="",
            cost_weight=0.3,
        )
        without_pricing = _compute_score(
            _ep("e1"),
            _STATS_DEFAULT,
            model="",
            cost_weight=0.0,
        )
        # Both paths should yield identical scores: pure base_score.
        assert with_empty == without_pricing

    def test_score_is_strictly_positive(self):
        """Every realistic combination produces a positive score. The
        endpoint with the highest score wins; a non-positive score would
        be ranked below cold-start endpoints (which return base_score>0)."""
        for model in ["gpt-4o", "ollama/llama3.3", "claude-opus-4-6", ""]:
            for cw in [0.0, 0.3, 0.7, 1.0]:
                score = _compute_score(
                    _ep("e"),
                    _STATS_DEFAULT,
                    model=model,
                    cost_weight=cw,
                )
                assert score > 0, f"score≤0 for model={model} cw={cw}"


# ── Full sort order on a realistic pool ───────────────────────────


class TestRankingOnPool:
    """The most useful test: given a realistic mixed-pool, assert the
    *order* in which endpoints would be selected. Catches subtle formula
    drift that individual-pair tests miss."""

    POOL = [
        ("free-fast", {"latency_ms": 200.0, "success_rate": 1.0}, "ollama/llama3.3"),
        ("cheap-fast", {"latency_ms": 250.0, "success_rate": 1.0}, "gpt-4o-mini"),
        ("expensive-fast", {"latency_ms": 200.0, "success_rate": 1.0}, "gpt-4o"),
        ("flaky-cheap", {"latency_ms": 250.0, "success_rate": 0.6}, "gpt-4o-mini"),
        (
            "expensive-slow",
            {"latency_ms": 1500.0, "success_rate": 1.0},
            "claude-opus-4-6",
        ),
    ]

    def _rank(self, cost_weight: float) -> list[str]:
        scored = [
            (_compute_score(_ep(eid), stats, model=m, cost_weight=cost_weight), eid)
            for eid, stats, m in self.POOL
        ]
        scored.sort(key=lambda x: x[0], reverse=True)
        return [eid for _, eid in scored]

    def test_pure_perf_ranking(self):
        """At cw=0, pure success²/latency ranking. Free-fast and
        expensive-fast tie on perf; tie-break is by Python's stable sort."""
        ranking = self._rank(cost_weight=0.0)
        # The two fast/reliable endpoints should be the top 2.
        assert set(ranking[:2]) == {"free-fast", "expensive-fast"}
        # Flaky should always lose to non-flaky at same latency.
        assert ranking.index("flaky-cheap") > ranking.index("cheap-fast")
        # Expensive-slow's 7.5x latency penalty puts it last.
        assert ranking[-1] == "expensive-slow"

    def test_moderate_cost_bias_ranking(self):
        """At cw=0.3 (default), free-fast pulls ahead of expensive-fast."""
        ranking = self._rank(cost_weight=0.3)
        assert ranking[0] == "free-fast"
        # Expensive-fast still beats cheap-fast on raw perf, but the cost
        # gap shrinks the lead. The order between them isn't load-bearing
        # here; the load-bearing claim is that free-fast is on top.
        assert "free-fast" in ranking[:1]

    def test_full_cost_bias_ranking(self):
        """At cw=1.0, free-fast dominates and even the cheap-fast paid
        models should be above expensive-fast."""
        ranking = self._rank(cost_weight=1.0)
        assert ranking[0] == "free-fast"
        assert ranking.index("cheap-fast") < ranking.index("expensive-fast")

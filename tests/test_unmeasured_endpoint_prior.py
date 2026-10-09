"""A brand-new endpoint does not win every routing decision.

get_endpoint_stats says latency 0.0 for an endpoint it has never seen, and the score
divides by latency (floored at 1 ms): a fresh endpoint scored ~1.0 against ~0.003 for
one measured at 300 ms, so it took every request until its first response arrived, a
whole burst under concurrency, including a dead endpoint registered a moment ago. The
"fastest" strategy picked it for the same reason.
"""

from types import SimpleNamespace

import pytest

from core import endpoint_stats
from plugins.default.smart_router import (
    UNMEASURED_PRIOR_LATENCY_MS,
    _compute_score,
    _stats_with_prior,
)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setattr(endpoint_stats, "_endpoint_stats", {})
    return endpoint_stats._endpoint_stats


def _ep(name):
    return SimpleNamespace(id=name)


def _measure(name, latency, success=1.0, count=10):
    endpoint_stats._endpoint_stats[name] = {
        "latency_ms": latency,
        "success_rate": success,
        "request_count": count,
    }


def test_an_unmeasured_endpoint_takes_the_median_of_the_measured_ones():
    _measure("a", 100.0)
    _measure("b", 300.0)
    _measure("c", 900.0)

    stats = _stats_with_prior([_ep("a"), _ep("b"), _ep("c"), _ep("new")])

    assert stats["new"]["latency_ms"] == 300.0
    assert stats["new"]["unmeasured"] is True
    assert stats["a"]["latency_ms"] == 100.0 and "unmeasured" not in stats["a"]


def test_with_nothing_measured_the_prior_is_neutral_not_zero():
    stats = _stats_with_prior([_ep("x"), _ep("y")])

    assert stats["x"]["latency_ms"] == UNMEASURED_PRIOR_LATENCY_MS > 1.0


def test_a_fresh_endpoint_no_longer_outscores_a_fast_measured_one():
    _measure("slow", 300.0)
    _measure("fast", 100.0)
    healthy = [_ep("slow"), _ep("fast"), _ep("new")]
    stats = _stats_with_prior(healthy)

    scores = {e.id: _compute_score(e, stats[e.id], cost_weight=0.0) for e in healthy}

    assert max(scores, key=scores.get) == "fast"
    assert scores["new"] < scores["fast"]
    # ...whereas the raw, unmeasured stats made it the runaway winner:
    raw = endpoint_stats.get_endpoint_stats("new")
    assert _compute_score(_ep("new"), raw, cost_weight=0.0) > scores["fast"]


def test_it_is_not_penalised_either_it_ties_the_typical_endpoint():
    _measure("a", 200.0)
    _measure("b", 200.0)
    healthy = [_ep("a"), _ep("b"), _ep("new")]
    stats = _stats_with_prior(healthy)

    assert _compute_score(_ep("new"), stats["new"], cost_weight=0.0) == pytest.approx(
        _compute_score(_ep("a"), stats["a"], cost_weight=0.0)
    )


def test_the_fastest_strategy_prefers_a_measured_provider_to_an_unseen_one():
    from core.model_resolver import resolve_model

    _measure("measured", 400.0)
    config = {
        "model_groups": {
            "grp": {
                "strategy": "fastest",
                "models": [
                    {"model": "m-unseen", "provider": "unseen"},
                    {"model": "m-measured", "provider": "measured"},
                ],
            }
        }
    }

    model, provider = resolve_model(config, "grp")

    assert (model, provider) == ("m-measured", "measured")

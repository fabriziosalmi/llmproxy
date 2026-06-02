"""Tests for core.metrics_history.MetricsHistory + sum_prometheus_counter."""

from __future__ import annotations

import pytest

from core.metrics_history import MetricsHistory, sum_prometheus_counter


class TestMetricsHistoryRingBuffer:
    def test_first_record_delta_emits_zero(self):
        """No prior reference — store 0 instead of "all of history collapsed
        into the first slot". Phantom-spike guard."""
        h = MetricsHistory(slots=24)
        h.record_delta("requests", 1000)
        snap = h.snapshot()
        # Pre-filled with 23 zeros + 1 from the record
        assert len(snap["requests"]) == 24
        assert snap["requests"][-1] == 0.0

    def test_subsequent_record_delta_emits_difference(self):
        h = MetricsHistory(slots=24)
        h.record_delta("requests", 1000)  # baseline
        h.record_delta("requests", 1042)  # +42
        h.record_delta("requests", 1100)  # +58
        snap = h.snapshot()
        assert snap["requests"][-2] == 42.0
        assert snap["requests"][-1] == 58.0

    def test_record_delta_clamps_negative_to_zero(self):
        """Counter went DOWN — process restart or instrument bug. Don't
        emit negative deltas; the sparkline would render bizarre."""
        h = MetricsHistory(slots=24)
        h.record_delta("requests", 1000)
        h.record_delta("requests", 50)  # restart
        snap = h.snapshot()
        assert snap["requests"][-1] == 0.0

    def test_record_gauge_emits_value_directly(self):
        h = MetricsHistory(slots=12)
        h.record_gauge("cost_usd", 1.23)
        h.record_gauge("cost_usd", 4.56)
        snap = h.snapshot()
        assert snap["cost_usd"][-2] == 1.23
        assert snap["cost_usd"][-1] == 4.56

    def test_buffer_caps_at_slots(self):
        h = MetricsHistory(slots=4)
        for i in range(10):
            h.record_gauge("x", float(i))
        snap = h.snapshot()
        assert len(snap["x"]) == 4
        # Oldest dropped, newest 4 retained.
        assert snap["x"] == [6.0, 7.0, 8.0, 9.0]

    def test_per_series_independence(self):
        h = MetricsHistory(slots=24)
        h.record_delta("requests", 100)
        h.record_gauge("cost_usd", 1.0)
        snap = h.snapshot()
        # Both series exist independently.
        assert "requests" in snap
        assert "cost_usd" in snap
        # Each is the right length.
        assert len(snap["requests"]) == 24
        assert len(snap["cost_usd"]) == 24

    def test_reset_clears_everything(self):
        h = MetricsHistory(slots=24)
        h.record_delta("requests", 100)
        h.record_gauge("cost_usd", 5.0)
        h.reset()
        assert h.snapshot() == {}
        # New record after reset starts fresh — no phantom delta from
        # the pre-reset high-water mark.
        h.record_delta("requests", 200)
        assert h.snapshot()["requests"][-1] == 0.0

    def test_slots_floor_at_one(self):
        """Defensive: pathological config (slots=0 or negative) clamps to 1
        instead of constructing an unusable buffer."""
        h = MetricsHistory(slots=0)
        assert h.slots == 1
        h.record_gauge("x", 1.0)
        assert h.snapshot()["x"] == [1.0]


class TestSumPrometheusCounter:
    def test_returns_zero_on_non_counter(self):
        """Defensive: pass anything weird, get 0.0 back, never raise."""
        assert sum_prometheus_counter(None) == 0.0
        assert sum_prometheus_counter("not a counter") == 0.0
        assert sum_prometheus_counter(object()) == 0.0

    def test_sums_real_counter_across_labels(self):
        """End-to-end against a fresh prometheus_client.Counter."""
        from prometheus_client import Counter, CollectorRegistry

        registry = CollectorRegistry()
        c = Counter(
            "test_counter_total",
            "Test counter",
            labelnames=("a", "b"),
            registry=registry,
        )
        c.labels(a="x", b="1").inc(5)
        c.labels(a="x", b="2").inc(3)
        c.labels(a="y", b="1").inc(7)
        # Sum across all label permutations.
        assert sum_prometheus_counter(c) == pytest.approx(15.0)

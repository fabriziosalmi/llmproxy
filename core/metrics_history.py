"""
Q.3 — Hourly ring buffer for KPI sparklines.

Prometheus counters are point-in-time + cumulative; the UI sparkline
needs a 24-point time series per metric. A background task snapshots
the relevant counters once per hour, this module owns the ring buffer
they land in, and the admin route serves it as
GET /api/v1/metrics/hourly-buckets.

Honest scope: ring buffer only. Restart loses history. For durable
long-window history wire Prometheus or any TSDB scrape against
/metrics — that's the right tool for that job. The ring buffer is a
visual affordance, not an analytics record.
"""

from __future__ import annotations

import logging
from collections import deque
from typing import Any

logger = logging.getLogger("llmproxy.metrics_history")


def sum_prometheus_counter(counter: Any) -> float:
    """Sum all label permutations of a Prometheus Counter into a single scalar.

    `Counter.collect()` returns metric families; we walk every sample whose
    name ends with `_total` (Prom convention for the cumulative value of a
    Counter — `_created` samples carry the timestamp, those are skipped).
    Defensive: returns 0.0 on any exception so a snapshot tick never crashes
    the proxy.
    """
    try:
        total = 0.0
        for metric in counter.collect():
            for sample in metric.samples:
                if sample.name.endswith("_total"):
                    total += float(sample.value)
        return total
    except Exception:  # noqa: BLE001 — never let a metrics read kill the loop
        return 0.0


class MetricsHistory:
    """Per-series ring buffer of fixed size (`slots`).

    Two record modes:
      record_delta(series, current_cumulative_value) — for monotonic counters.
        Stores the delta since the previous tick; resets the high-water mark.
      record_gauge(series, current_value) — for point-in-time values
        (e.g. agent.total_cost_today, pool_healthy).

    `snapshot()` returns a copy as plain lists so the admin route can pass
    it straight to JSON without exposing the deque.
    """

    def __init__(self, slots: int = 24) -> None:
        self.slots = max(1, int(slots))
        self.buckets: dict[str, deque[float]] = {}
        self._last_snapshot: dict[str, float] = {}

    def record_delta(self, series: str, current_value: float) -> None:
        prev = self._last_snapshot.get(series)
        # First tick has no prior reference — store 0 so the buffer fills
        # without phantom spikes from "all of history collapsed into the
        # first slot".
        delta = 0.0 if prev is None else max(0.0, current_value - prev)
        self._last_snapshot[series] = current_value
        self._append(series, delta)

    def record_gauge(self, series: str, current_value: float) -> None:
        self._append(series, current_value)

    def _append(self, series: str, value: float) -> None:
        if series not in self.buckets:
            # Pre-fill with 0.0 so the array is always `slots` long — the
            # frontend Sparkline expects ≥ 2 points and needs a stable shape.
            self.buckets[series] = deque([0.0] * self.slots, maxlen=self.slots)
        self.buckets[series].append(float(value))

    def snapshot(self) -> dict[str, list[float]]:
        return {k: list(v) for k, v in self.buckets.items()}

    def reset(self) -> None:
        """Clear all buckets — used by tests + on config reload if needed."""
        self.buckets.clear()
        self._last_snapshot.clear()

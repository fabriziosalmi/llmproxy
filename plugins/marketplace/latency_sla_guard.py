"""
LLMPROXY Marketplace Plugin — Latency SLA Guard

Post-flight latency measurement and SLA enforcement.
Computes Time-To-First-Byte (TTFT) and total request latency from
timestamps set by the proxy pipeline, then flags SLA violations.

SLA tiers:
  - P50 target: 50% of requests should complete within ttft_p50_ms / total_p50_ms
  - P95 target: 95% of requests should complete within ttft_p95_ms / total_p95_ms
  - Hard limit: Requests exceeding hard_limit_ms are flagged as SLA breaches

Outputs:
  - ctx.metadata["_latency_ms"]: total request latency
  - ctx.metadata["_ttft_ms"]: time to first byte (if available)
  - ctx.metadata["_sla_status"]: "ok" | "warning" | "breach"
  - ctx.metadata["_sla_violations"]: list of violated thresholds

The plugin maintains a rolling window of latency samples for percentile
calculation, exposed via the SOC dashboard.

Config (via manifest ui_schema):
  - ttft_p95_ms: int (500) — TTFT P95 target in ms
  - total_p95_ms: int (3000) — total latency P95 target in ms
  - hard_limit_ms: int (10000) — hard SLA breach threshold
  - window_size: int (500) — rolling window for percentile calculation
"""

import time
from collections import deque
from typing import Dict, Any, List

from core.plugin_sdk import BasePlugin, PluginResponse, PluginHook
from core.plugin_engine import PluginContext


class LatencySlaGuard(BasePlugin):
    name = "latency_sla_guard"
    hook = PluginHook.POST_FLIGHT
    version = "1.0.0"
    author = "llmproxy"
    description = "Measures TTFT and total latency, flags SLA violations"
    timeout_ms = 2  # Pure arithmetic, no I/O

    def __init__(self, config: Dict[str, Any] = None):
        super().__init__(config)
        self.ttft_p95_ms: int = self.config.get("ttft_p95_ms", 500)
        self.total_p95_ms: int = self.config.get("total_p95_ms", 3000)
        self.hard_limit_ms: int = self.config.get("hard_limit_ms", 10000)
        self.window_size: int = self.config.get("window_size", 500)

        # Rolling windows for percentile tracking
        self._ttft_samples: deque = deque(maxlen=self.window_size)
        self._total_samples: deque = deque(maxlen=self.window_size)

        # Counters
        self._total_requests: int = 0
        self._sla_warnings: int = 0
        self._sla_breaches: int = 0

    def _compute_latency(self, ctx: PluginContext) -> Dict[str, float]:
        """Extract latency measurements from request timestamps."""
        now = time.time()

        # Total latency: from request start to now
        request_start = ctx.metadata.get("_request_start_time")
        total_ms = (now - request_start) * 1000 if request_start else 0.0

        # TTFT: from request start to first byte received
        ttft_time = ctx.metadata.get("_ttft_time")
        ttft_ms = (ttft_time - request_start) * 1000 if (ttft_time and request_start) else None

        return {"total_ms": round(total_ms, 2), "ttft_ms": round(ttft_ms, 2) if ttft_ms else None}

    def _evaluate_sla(self, total_ms: float, ttft_ms: float | None) -> tuple:
        """Evaluate latency against SLA targets. Returns (status, violations)."""
        violations: List[str] = []

        # Hard limit breach
        if total_ms > self.hard_limit_ms:
            violations.append(f"total>{self.hard_limit_ms}ms")
            return "breach", violations

        # P95 target violations
        if total_ms > self.total_p95_ms:
            violations.append(f"total>{self.total_p95_ms}ms(P95)")

        if ttft_ms is not None and ttft_ms > self.ttft_p95_ms:
            violations.append(f"ttft>{self.ttft_p95_ms}ms(P95)")

        if violations:
            return "warning", violations

        return "ok", []

    def _percentiles(self, samples: deque) -> Dict[str, float]:
        """Compute P50/P95/P99 from a deque of samples."""
        if not samples:
            return {"p50": 0.0, "p95": 0.0, "p99": 0.0}
        sorted_samples = sorted(samples)
        n = len(sorted_samples)
        return {
            "p50": round(sorted_samples[int(n * 0.50)], 2),
            "p95": round(sorted_samples[min(int(n * 0.95), n - 1)], 2),
            "p99": round(sorted_samples[min(int(n * 0.99), n - 1)], 2),
        }

    def get_sla_stats(self) -> Dict[str, Any]:
        """Public stats for SOC dashboard / admin API."""
        return {
            "total_requests": self._total_requests,
            "sla_warnings": self._sla_warnings,
            "sla_breaches": self._sla_breaches,
            "breach_rate": round(
                self._sla_breaches / max(self._total_requests, 1), 4
            ),
            "ttft_percentiles": self._percentiles(self._ttft_samples),
            "total_percentiles": self._percentiles(self._total_samples),
            "targets": {
                "ttft_p95_ms": self.ttft_p95_ms,
                "total_p95_ms": self.total_p95_ms,
                "hard_limit_ms": self.hard_limit_ms,
            },
        }

    async def execute(self, ctx: PluginContext) -> PluginResponse:
        self._total_requests += 1

        # Skip latency check for cached responses (instant by definition)
        if ctx.metadata.get("_cache_status") == "HIT":
            ctx.metadata["_sla_status"] = "cached"
            return PluginResponse.passthrough()

        # Compute latency
        latency = self._compute_latency(ctx)
        total_ms = latency["total_ms"]
        ttft_ms = latency["ttft_ms"]

        # Record samples
        if total_ms > 0:
            self._total_samples.append(total_ms)
        if ttft_ms is not None:
            self._ttft_samples.append(ttft_ms)

        # Evaluate SLA
        sla_status, violations = self._evaluate_sla(total_ms, ttft_ms)

        if sla_status == "warning":
            self._sla_warnings += 1
        elif sla_status == "breach":
            self._sla_breaches += 1

        # Enrich metadata
        ctx.metadata["_latency_ms"] = total_ms
        if ttft_ms is not None:
            ctx.metadata["_ttft_ms"] = ttft_ms
        ctx.metadata["_sla_status"] = sla_status
        if violations:
            ctx.metadata["_sla_violations"] = violations

        return PluginResponse.passthrough()

    async def on_load(self):
        self.logger.info(
            f"LatencySlaGuard loaded: ttft_p95={self.ttft_p95_ms}ms, "
            f"total_p95={self.total_p95_ms}ms, hard_limit={self.hard_limit_ms}ms"
        )

    async def on_unload(self):
        self._ttft_samples.clear()
        self._total_samples.clear()
        self.logger.info(
            f"LatencySlaGuard unloaded: {self._total_requests} requests tracked, "
            f"{self._sla_breaches} breaches"
        )

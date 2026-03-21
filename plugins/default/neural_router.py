"""
Neural Router — Ring 3: Routing & Load Balancing

Selects a healthy upstream endpoint using one of three strategies:
  - Priority steering (priority_mode=True): highest-priority endpoint wins
  - Smart weighted (default): score = success_rate / (latency_ms * cost_weight)
  - Round-robin fallback: when no latency data exists yet (cold start)

Endpoints are filtered by circuit breaker state before selection.
Latency and success_rate are updated after each request via _update_endpoint_stats().
"""

import logging
from core.plugin_engine import PluginContext

logger = logging.getLogger("plugin.neural_router")

# Module-level round-robin index — persists across requests, reset is harmless
_rr_index = 0

# In-memory endpoint stats (EMA-smoothed) — survives across requests
# Maps endpoint_id → {"latency_ms": float, "success_rate": float, "request_count": int}
_endpoint_stats: dict = {}

# EMA smoothing factor (0.1 = slow adaptation, 0.3 = fast adaptation)
_EMA_ALPHA = 0.2


def update_endpoint_stats(endpoint_id: str, latency_ms: float, success: bool):
    """Update endpoint performance stats with exponential moving average.

    Called after each completed request from rotator.py.
    """
    if endpoint_id not in _endpoint_stats:
        _endpoint_stats[endpoint_id] = {
            "latency_ms": latency_ms,
            "success_rate": 1.0 if success else 0.0,
            "request_count": 0,
        }

    stats = _endpoint_stats[endpoint_id]
    stats["latency_ms"] = _EMA_ALPHA * latency_ms + (1 - _EMA_ALPHA) * stats["latency_ms"]
    stats["success_rate"] = _EMA_ALPHA * (1.0 if success else 0.0) + (1 - _EMA_ALPHA) * stats["success_rate"]
    stats["request_count"] += 1


def get_endpoint_stats(endpoint_id: str) -> dict:
    """Get current stats for an endpoint (for API/dashboard)."""
    return _endpoint_stats.get(endpoint_id, {
        "latency_ms": 0.0,
        "success_rate": 1.0,
        "request_count": 0,
    })


def _compute_score(endpoint, stats: dict) -> float:
    """Compute routing score: higher = better endpoint.

    score = success_rate^2 / max(latency_ms, 1)

    - success_rate squared to penalize unreliable endpoints more aggressively
    - latency_ms in denominator favors faster endpoints
    - Minimum latency of 1ms to avoid division by zero
    """
    success = stats.get("success_rate", 1.0)
    latency = max(stats.get("latency_ms", 500.0), 1.0)  # default 500ms for unknown
    return (success ** 2) / latency


async def select_endpoint(ctx: PluginContext):
    """Ring 3: Routing & Load Balancing."""
    global _rr_index

    rotator = ctx.metadata.get("rotator")

    # Fetch verified pool from store
    pool = await rotator.store.get_pool()
    if not pool:
        ctx.error = "No verified endpoints available."
        ctx.stop_chain = True
        return

    # Filter to endpoints with CLOSED/HALF_OPEN circuit breakers
    healthy = [e for e in pool if rotator.circuit_manager.get_breaker(e.id).can_execute()]
    if not healthy:
        ctx.error = "All endpoints offline (circuit OPEN)."
        ctx.stop_chain = True
        return

    # Steering strategy selection
    if rotator.priority_mode:
        # Priority mode: highest-priority endpoint wins
        healthy.sort(key=lambda e: e.metadata.get("priority", 0), reverse=True)
        target = healthy[0]
    elif any(e.id in _endpoint_stats for e in healthy):
        # Smart weighted: score-based selection using real performance data
        scored = []
        for e in healthy:
            stats = get_endpoint_stats(e.id)
            score = _compute_score(e, stats)
            scored.append((score, e, stats))

        scored.sort(key=lambda x: x[0], reverse=True)
        target = scored[0][1]
        best_stats = scored[0][2]

        logger.debug(
            f"Smart route: {target.id} (score={scored[0][0]:.4f}, "
            f"latency={best_stats['latency_ms']:.1f}ms, "
            f"success={best_stats['success_rate']:.2%})"
        )
        ctx.metadata["_routing_strategy"] = "smart_weighted"
        ctx.metadata["_routing_score"] = round(scored[0][0], 6)
    else:
        # Cold start: round-robin until we have performance data
        target = healthy[_rr_index % len(healthy)]
        _rr_index = (_rr_index + 1) % max(len(healthy), 1)
        ctx.metadata["_routing_strategy"] = "round_robin"

    ctx.metadata["target_endpoint"] = target
    await rotator._add_log(f"Routing to: {target.id} ({target.url})", level="PROXY")

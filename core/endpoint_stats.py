"""Endpoint performance statistics — latency and success rate, EMA-smoothed.

This is routing infrastructure, not plugin behaviour, but it lived in
plugins/default/smart_router.py for historical reasons. That put the dependency
the wrong way round: proxy/request_pipeline.py imported
plugins.default.neural_router at module level to reach it, so the extension
package became a hard requirement of the core it extends — deleting or
disabling that plugin stopped the dispatch module importing at all, which
takes down every proxied request rather than degrading a routing heuristic.
core/ and proxy/ reached into plugins/ in three further places for the same
reason, and since smart_router imports core.plugin_engine and core.pricing,
the two packages formed a cycle.

The state and the functions live here now. plugins/default/smart_router.py
re-exports them, so plugins and any external caller keep working unchanged,
and the arrow points inward: plugins depend on core, not the reverse.

The stats are per-process and in-memory, EMA-smoothed with alpha 0.2, guarded
by a single lock. When Redis is configured they are mirrored there so several
processes converge; without it each process routes on what it has seen.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

logger = logging.getLogger("llmproxy.endpoint_stats")

# In-memory endpoint stats (EMA-smoothed) — survives across requests.
# Maps endpoint_id -> {"latency_ms": float, "success_rate": float, "request_count": int}
_endpoint_stats: dict = {}

# Lock protecting _endpoint_stats from concurrent access.
_stats_lock = asyncio.Lock()

# EMA smoothing factor (0.1 = slow adaptation, 0.3 = fast adaptation)
_EMA_ALPHA = 0.2


async def update_endpoint_stats(
    endpoint_id: str,
    latency_ms: float,
    success: bool,
    redis_client: Optional[Any] = None,
):
    """Update endpoint performance stats with exponential moving average.

    Called after each completed request from rotator.py.
    """
    async with _stats_lock:
        if endpoint_id not in _endpoint_stats:
            _endpoint_stats[endpoint_id] = {
                "latency_ms": latency_ms,
                "success_rate": 1.0 if success else 0.0,
                "request_count": 0,
            }

        stats = _endpoint_stats[endpoint_id]
        stats["latency_ms"] = (
            _EMA_ALPHA * latency_ms + (1 - _EMA_ALPHA) * stats["latency_ms"]
        )
        stats["success_rate"] = (
            _EMA_ALPHA * (1.0 if success else 0.0)
            + (1 - _EMA_ALPHA) * stats["success_rate"]
        )
        stats["request_count"] += 1

    if redis_client:
        try:
            res = await redis_client.hgetall(f"ep:stats:{endpoint_id}")
            if res:
                db_lat = float(res.get("latency_ms", latency_ms))
                db_succ = float(res.get("success_rate", 1.0 if success else 0.0))
                db_count = int(res.get("request_count", 0))
            else:
                db_lat = latency_ms
                db_succ = 1.0 if success else 0.0
                db_count = 0

            new_lat = _EMA_ALPHA * latency_ms + (1 - _EMA_ALPHA) * db_lat
            new_succ = _EMA_ALPHA * (1.0 if success else 0.0) + (1 - _EMA_ALPHA) * db_succ
            new_count = db_count + 1

            await redis_client.hset(
                f"ep:stats:{endpoint_id}",
                mapping={
                    "latency_ms": str(new_lat),
                    "success_rate": str(new_succ),
                    "request_count": str(new_count),
                },
            )
        except Exception as e:
            logger.warning(f"Failed to update Redis stats for {endpoint_id}: {e}")


async def sync_endpoint_stats_from_redis(redis_client):
    """Pulls all endpoint stats from Redis and updates local _endpoint_stats."""
    async with _stats_lock:
        try:
            keys = await redis_client.keys("ep:stats:*")
            for key in keys:
                endpoint_id = key.split(":")[-1]
                res = await redis_client.hgetall(key)
                if res:
                    _endpoint_stats[endpoint_id] = {
                        "latency_ms": float(res.get("latency_ms", 0.0)),
                        "success_rate": float(res.get("success_rate", 1.0)),
                        "request_count": int(res.get("request_count", 0)),
                    }
        except Exception as e:
            logger.warning(f"Failed to sync endpoint stats from Redis: {e}")


def get_endpoint_stats(endpoint_id: str) -> dict[str, Any]:
    """Get current stats for an endpoint (for API/dashboard).

    Note: snapshot read — may see slightly stale data without lock, acceptable for dashboards.
    """
    result: dict[str, Any] = _endpoint_stats.get(
        endpoint_id,
        {
            "latency_ms": 0.0,
            "success_rate": 1.0,
            "request_count": 0,
        },
    )
    return result

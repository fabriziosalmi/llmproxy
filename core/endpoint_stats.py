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
from typing import Any

logger = logging.getLogger("llmproxy.endpoint_stats")

# In-memory endpoint stats (EMA-smoothed) — survives across requests.
# Maps endpoint_id -> {"latency_ms": float, "success_rate": float, "request_count": int}
_endpoint_stats: dict = {}

# Lock protecting _endpoint_stats from concurrent access.
_stats_lock = asyncio.Lock()

# EMA smoothing factor (0.1 = slow adaptation, 0.3 = fast adaptation)
_EMA_ALPHA = 0.2

_REDIS_KEY_PREFIX = "ep:stats:"

# One endpoint's shared stats are updated by every request task of every
# replica. A read (HGETALL), a compute and a write (HSET) as separate round trips
# lets two updaters read the same count and both write count+1, losing an
# observation; run as one script the whole read-modify-write is atomic on the
# Redis side. The first observation seeds the average, as before.
_REDIS_UPDATE_STATS = """
local lat, succ, alpha = tonumber(ARGV[1]), tonumber(ARGV[2]), tonumber(ARGV[3])
local cur = redis.call('HMGET', KEYS[1], 'latency_ms', 'success_rate', 'request_count')
local l = tonumber(cur[1])
local s = tonumber(cur[2])
local c = tonumber(cur[3]) or 0
if l == nil then l = lat end
if s == nil then s = succ end
l = alpha * lat + (1 - alpha) * l
s = alpha * succ + (1 - alpha) * s
c = c + 1
redis.call('HSET', KEYS[1],
    'latency_ms', string.format('%.17g', l),
    'success_rate', string.format('%.17g', s),
    'request_count', string.format('%d', c))
return c
"""


async def update_endpoint_stats(
    endpoint_id: str,
    latency_ms: float,
    success: bool,
    redis_client: Any | None = None,
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
            await redis_client.eval(
                _REDIS_UPDATE_STATS,
                1,
                f"{_REDIS_KEY_PREFIX}{endpoint_id}",
                latency_ms,
                1.0 if success else 0.0,
                _EMA_ALPHA,
            )
        except Exception as e:
            logger.warning(f"Failed to update Redis stats for {endpoint_id}: {e}")


async def sync_endpoint_stats_from_redis(redis_client):
    """Pulls all endpoint stats from Redis and updates local _endpoint_stats.

    The Redis round trips (a SCAN, then one pipelined batch of HGETALLs) happen
    before the lock is taken: update_endpoint_stats shares _stats_lock, so
    holding it across the network stalled every request's stats update for
    1 + N round trips on each sync, and KEYS walks the whole keyspace. The lock
    now covers only the dictionary assignment.
    """
    try:
        keys = [
            key
            async for key in redis_client.scan_iter(
                match=f"{_REDIS_KEY_PREFIX}*", count=200
            )
        ]
        if not keys:
            return
        pipe = redis_client.pipeline(transaction=False)
        for key in keys:
            pipe.hgetall(key)
        hashes = await pipe.execute()
    except Exception as e:
        logger.warning(f"Failed to sync endpoint stats from Redis: {e}")
        return

    fresh: dict[str, dict[str, Any]] = {}
    for key, res in zip(keys, hashes, strict=True):
        if not res:
            continue
        # Strip the prefix rather than splitting on ":": an endpoint id may
        # itself contain colons (host:port).
        endpoint_id = key[len(_REDIS_KEY_PREFIX) :]
        try:
            fresh[endpoint_id] = {
                "latency_ms": float(res.get("latency_ms", 0.0)),
                "success_rate": float(res.get("success_rate", 1.0)),
                "request_count": int(res.get("request_count", 0)),
            }
        except (TypeError, ValueError) as e:
            logger.warning(f"Skipping malformed Redis stats for {endpoint_id}: {e}")

    async with _stats_lock:
        _endpoint_stats.update(fresh)


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

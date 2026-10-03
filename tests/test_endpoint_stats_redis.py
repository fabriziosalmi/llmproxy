"""Shared endpoint stats in Redis: atomic updates, and a sync that does not stall them.

update_endpoint_stats read the Redis hash (HGETALL), computed the new average and
wrote it back (HSET) as separate round trips with no lock, so two updaters for the
same endpoint, two tasks or two replicas, read the same request_count and both
wrote count+1: an observation was lost. sync_endpoint_stats_from_redis held the
asyncio lock that every update shares across a KEYS scan and one HGETALL per
endpoint, so each 5-second sync stalled the stats update on every request.

The fake-client tests always run. The real-Redis tests need TEST_REDIS_URL
(for example redis://127.0.0.1:6379/15) and skip without it; CI provides one.
"""

import asyncio
import os

import pytest

from core import endpoint_stats
from core.endpoint_stats import (
    _EMA_ALPHA,
    sync_endpoint_stats_from_redis,
    update_endpoint_stats,
)

TEST_REDIS_URL = os.environ.get("TEST_REDIS_URL", "")


@pytest.fixture(autouse=True)
def _clean_local_stats():
    endpoint_stats._endpoint_stats.clear()
    yield
    endpoint_stats._endpoint_stats.clear()


# ── fake client ─────────────────────────────────────────────────────────────


class _Pipeline:
    def __init__(self, store, on_execute):
        self._store, self._keys, self._on_execute = store, [], on_execute

    def hgetall(self, key):
        self._keys.append(key)

    async def execute(self):
        self._on_execute()
        return [dict(self._store.get(k, {})) for k in self._keys]


class FakeRedis:
    """Records calls; keeps hashes in a dict. Not a Redis: it never runs Lua."""

    def __init__(self, hashes=None):
        self.hashes = hashes or {}
        self.calls = []
        self.lock_held_during_io = []

    def _note_io(self):
        self.lock_held_during_io.append(endpoint_stats._stats_lock.locked())

    async def eval(self, script, numkeys, *args):
        self.calls.append(("eval", numkeys, args))

    async def hgetall(self, key):
        self.calls.append(("hgetall", key))
        return {}

    async def hset(self, *a, **k):
        self.calls.append(("hset", a))

    async def keys(self, pattern):
        self.calls.append(("keys", pattern))
        return []

    async def scan_iter(self, match=None, count=None):
        self._note_io()
        for key in list(self.hashes):
            if match is None or key.startswith(match.rstrip("*")):
                yield key

    def pipeline(self, transaction=False):
        return _Pipeline(self.hashes, self._note_io)


async def test_update_is_a_single_atomic_script_call():
    redis = FakeRedis()

    await update_endpoint_stats("ep1", 120.0, True, redis_client=redis)

    assert [c[0] for c in redis.calls] == ["eval"], (
        "the Redis update must be one script call, not a read then a write"
    )
    _, numkeys, args = redis.calls[0]
    assert numkeys == 1
    assert args == ("ep:stats:ep1", 120.0, 1.0, _EMA_ALPHA)


async def test_a_failed_update_is_logged_and_does_not_raise(caplog):
    class Down(FakeRedis):
        async def eval(self, *a):
            raise ConnectionError("redis is down")

    await update_endpoint_stats("ep1", 1.0, True, redis_client=Down())

    assert "Failed to update Redis stats for ep1" in caplog.text
    # The local average still moved.
    assert endpoint_stats._endpoint_stats["ep1"]["request_count"] == 1


async def test_sync_does_not_hold_the_stats_lock_during_redis_io():
    redis = FakeRedis(
        {"ep:stats:a": {"latency_ms": "10", "success_rate": "0.9", "request_count": "3"}}
    )

    await sync_endpoint_stats_from_redis(redis)

    assert redis.lock_held_during_io and not any(redis.lock_held_during_io)
    assert ("keys", "ep:stats:*") not in redis.calls, "KEYS walks the whole keyspace"


async def test_sync_copies_remote_stats_over_the_local_ones():
    endpoint_stats._endpoint_stats["a"] = {
        "latency_ms": 1.0, "success_rate": 1.0, "request_count": 1
    }
    redis = FakeRedis(
        {"ep:stats:a": {"latency_ms": "10.5", "success_rate": "0.5", "request_count": "7"}}
    )

    await sync_endpoint_stats_from_redis(redis)

    assert endpoint_stats._endpoint_stats["a"] == {
        "latency_ms": 10.5, "success_rate": 0.5, "request_count": 7
    }


async def test_an_endpoint_id_containing_colons_survives_sync():
    redis = FakeRedis(
        {"ep:stats:lmstudio:1234": {"latency_ms": "5", "success_rate": "1", "request_count": "1"}}
    )

    await sync_endpoint_stats_from_redis(redis)

    assert "lmstudio:1234" in endpoint_stats._endpoint_stats


async def test_a_malformed_remote_hash_is_skipped_not_fatal(caplog):
    redis = FakeRedis(
        {
            "ep:stats:bad": {"latency_ms": "not-a-number"},
            "ep:stats:good": {"latency_ms": "2", "success_rate": "1", "request_count": "1"},
        }
    )

    await sync_endpoint_stats_from_redis(redis)

    assert "good" in endpoint_stats._endpoint_stats
    assert "bad" not in endpoint_stats._endpoint_stats
    assert "Skipping malformed Redis stats for bad" in caplog.text


async def test_sync_with_nothing_in_redis_changes_nothing():
    await sync_endpoint_stats_from_redis(FakeRedis())
    assert endpoint_stats._endpoint_stats == {}


async def test_a_sync_failure_is_logged_and_does_not_raise(caplog):
    class Down(FakeRedis):
        async def scan_iter(self, match=None, count=None):
            raise ConnectionError("redis is down")
            yield  # pragma: no cover

    await sync_endpoint_stats_from_redis(Down())

    assert "Failed to sync endpoint stats from Redis" in caplog.text


# ── real Redis ──────────────────────────────────────────────────────────────

requires_redis = pytest.mark.skipif(
    not TEST_REDIS_URL, reason="TEST_REDIS_URL not set — start a Redis to run these"
)


@pytest.fixture
async def redis():
    aioredis = pytest.importorskip("redis.asyncio")
    # redis-py 8 caps the default pool (100 connections); the concurrency test
    # puts 200 updates in flight at once, so the pool must be able to serve them.
    client = aioredis.from_url(TEST_REDIS_URL, decode_responses=True, max_connections=500)
    await client.flushdb()
    yield client
    await client.flushdb()
    await client.aclose()


@requires_redis
async def test_concurrent_updates_lose_no_observation(redis):
    n = 200
    await asyncio.gather(
        *[update_endpoint_stats("hot", 100.0, True, redis_client=redis) for _ in range(n)]
    )

    stored = await redis.hgetall("ep:stats:hot")
    assert int(stored["request_count"]) == n


@requires_redis
async def test_the_stored_average_follows_the_documented_ema(redis):
    samples = [(100.0, True), (200.0, False), (50.0, True), (400.0, True)]
    lat = succ = None
    for latency, ok in samples:
        await update_endpoint_stats("ep", latency, ok, redis_client=redis)
        x = 1.0 if ok else 0.0
        lat = latency if lat is None else _EMA_ALPHA * latency + (1 - _EMA_ALPHA) * lat
        succ = x if succ is None else _EMA_ALPHA * x + (1 - _EMA_ALPHA) * succ

    stored = await redis.hgetall("ep:stats:ep")
    assert float(stored["latency_ms"]) == pytest.approx(lat)
    assert float(stored["success_rate"]) == pytest.approx(succ)
    assert int(stored["request_count"]) == len(samples)


@requires_redis
async def test_sync_round_trips_through_a_real_redis(redis):
    await update_endpoint_stats("a", 100.0, True, redis_client=redis)
    await update_endpoint_stats("b:9000", 300.0, False, redis_client=redis)
    endpoint_stats._endpoint_stats.clear()

    await sync_endpoint_stats_from_redis(redis)

    assert endpoint_stats._endpoint_stats["a"]["latency_ms"] == pytest.approx(100.0)
    assert endpoint_stats._endpoint_stats["b:9000"]["success_rate"] == pytest.approx(0.0)

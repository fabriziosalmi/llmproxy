"""The reset and clear routes work against the real objects, not just mocks.

Three admin routes were broken whenever the real collaborator was present, and
every test passed because the agent was a MagicMock that accepts any attribute:

* POST /api/v1/cache/clear called NegativeCache.clear(), which did not exist;
* POST /api/v1/security/reset cleared ``threat_ledger._by_ip`` / ``_by_key``,
  which do not exist (the ledgers are ``_ip_ledger`` / ``_key_ledger``);
* POST /api/v1/circuit-breaker/{id}/reset took ``cb._lock`` and set ``state``
  and ``failure_count`` on the breaker, which a Redis breaker (state in four
  cb:<name>:* keys) has none of.

All three answered 500. Found by typing the route modules against the orchestrator.
"""

import os
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import FastAPI

from core.cache import NegativeCache
from core.circuit_breaker import (
    CircuitManager,
    CircuitState,
    LocalCircuitBreaker,
    RedisCircuitBreaker,
)
from core.threat_ledger import ThreatLedger
from tests.conftest import minimal_config

TEST_REDIS_URL = os.environ.get("TEST_REDIS_URL", "")


def _client(agent):
    from proxy.routes.admin import create_router

    app = FastAPI()
    app.include_router(create_router(agent))
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://t",
    )


def _agent(**attrs):
    agent = MagicMock()
    agent.config = minimal_config(auth_enabled=False)
    for k, v in attrs.items():
        setattr(agent, k, v)
    return agent


# ── cache clear ─────────────────────────────────────────────────────────────


def test_negative_cache_clear_drops_everything_and_counts():
    cache = NegativeCache(maxsize=10, ttl=300)
    for i in range(3):
        cache.add({"messages": [{"role": "user", "content": f"bad {i}"}]}, "blocked")

    assert cache.clear() == 3
    assert cache.check({"messages": [{"role": "user", "content": "bad 0"}]}) is None


def test_a_disabled_negative_cache_clears_to_zero():
    assert NegativeCache(enabled=False).clear() == 0


async def test_cache_clear_route_with_a_real_negative_cache():
    cache = NegativeCache()
    cache.add({"messages": [{"role": "user", "content": "x"}]}, "blocked")
    agent = _agent(negative_cache=cache, cache_backend=None)

    async with _client(agent) as c:
        resp = await c.post("/api/v1/cache/clear")

    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "cleared"
    assert resp.json()["negative_cache"] == "cleared 1 entries"
    assert cache.check({"messages": [{"role": "user", "content": "x"}]}) is None


# ── security reset ──────────────────────────────────────────────────────────


def test_threat_ledger_clear_forgets_ips_and_keys():
    ledger = ThreatLedger()
    ledger.record(ip="1.2.3.4", key_prefix="sk-a", score=5.0)
    ledger.record(ip="5.6.7.8", key_prefix="", score=5.0)

    assert ledger.clear() == {"ips": 2, "keys": 1}
    assert len(ledger._ip_ledger) == 0 and len(ledger._key_ledger) == 0
    assert ledger.clear() == {"ips": 0, "keys": 0}


async def test_security_reset_route_with_a_real_threat_ledger():
    ledger = ThreatLedger()
    ledger.record(ip="1.2.3.4", key_prefix="sk-a", score=5.0)
    security = MagicMock()
    security.session_memory = {"s1": [1], "s2": [2]}
    security.threat_ledger = ledger
    agent = _agent(security=security)

    async with _client(agent) as c:
        resp = await c.post("/api/v1/security/reset")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "reset" and body["sessions_cleared"] == 2
    assert body["threat_ledger"] == "cleared"
    assert body["threat_ledger_dropped"] == {"ips": 1, "keys": 1}
    assert security.session_memory == {} and len(ledger._ip_ledger) == 0


async def test_security_reset_without_a_ledger_still_clears_sessions():
    security = MagicMock()
    security.session_memory = {"s1": [1]}
    security.threat_ledger = None

    async with _client(_agent(security=security)) as c:
        resp = await c.post("/api/v1/security/reset")

    assert resp.status_code == 200 and "threat_ledger" not in resp.json()


# ── circuit breaker reset ───────────────────────────────────────────────────


async def test_local_breaker_reset_closes_it_and_notifies():
    changes = []
    cb = LocalCircuitBreaker(
        "ep", failure_threshold=2, on_state_change=lambda n, o, new: changes.append((o, new))
    )
    await cb.report_failure()
    await cb.report_failure()
    assert cb.state == CircuitState.OPEN and not await cb.can_execute()

    await cb.reset()

    assert cb.state == CircuitState.CLOSED and cb.failure_count == 0
    assert await cb.can_execute()
    assert changes[-1] == ("open", "closed")


async def test_resetting_a_closed_local_breaker_does_not_notify():
    changes = []
    cb = LocalCircuitBreaker("ep", on_state_change=lambda *a: changes.append(a))
    await cb.reset()
    assert changes == []


async def test_reset_route_with_a_real_local_breaker():
    manager = CircuitManager(redis_client=None)
    cb = await manager.get_breaker("ep1")
    for _ in range(5):
        await cb.report_failure()
    assert not await cb.can_execute()

    async with _client(_agent(circuit_manager=manager)) as c:
        resp = await c.post("/api/v1/circuit-breaker/ep1/reset")

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"status": "reset", "endpoint": "ep1", "state": "CLOSED"}
    assert await cb.can_execute()


async def test_a_redis_failure_is_a_502_not_a_false_closed():
    class Down:
        async def delete(self, *keys):
            raise ConnectionError("redis is down")

    cb = RedisCircuitBreaker(Down(), {}, name="ep1")
    manager = MagicMock()
    manager.get_breaker = AsyncMock(return_value=cb)

    async with _client(_agent(circuit_manager=manager)) as c:
        resp = await c.post("/api/v1/circuit-breaker/ep1/reset")

    assert resp.status_code == 502
    assert "CLOSED" not in resp.text


async def test_redis_breaker_reset_deletes_its_four_keys_and_the_local_fallback():
    deleted = []

    class Recorder:
        async def delete(self, *keys):
            deleted.extend(keys)

    cb = RedisCircuitBreaker(Recorder(), {}, name="ep1")
    await cb._local_fallback.report_failure()
    cb._local_fallback.failure_count = 9

    await cb.reset()

    assert deleted == ["cb:ep1:state", "cb:ep1:fail", "cb:ep1:last", "cb:ep1:probe"]
    assert cb._local_fallback.failure_count == 0


@pytest.fixture
async def real_redis():
    if not TEST_REDIS_URL:
        pytest.skip("TEST_REDIS_URL not set")
    aioredis = pytest.importorskip("redis.asyncio")
    client = aioredis.from_url(TEST_REDIS_URL, decode_responses=True)
    await client.flushdb()
    yield client
    await client.flushdb()
    await client.aclose()


async def test_reset_route_with_a_real_redis_breaker(real_redis):
    manager = CircuitManager(redis_client=real_redis)
    cb = await manager.get_breaker("ep1")
    assert isinstance(cb, RedisCircuitBreaker)
    for _ in range(5):
        await cb.report_failure()
    assert (await cb.get_state_info())["state"] == "open"
    assert not await cb.can_execute()

    async with _client(_agent(circuit_manager=manager)) as c:
        resp = await c.post("/api/v1/circuit-breaker/ep1/reset")

    assert resp.status_code == 200, resp.text
    info = await cb.get_state_info()
    assert info["state"] == "closed" and info["failure_count"] == 0
    assert await cb.can_execute()

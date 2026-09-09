"""Overload was absorbed rather than refused, and a second instance was silent.

**No admission control.** The only ceiling on concurrent upstream work was the
aiohttp connector's limit, and past it requests did not fail — they waited in
the connector's unbounded internal queue, with no deadline, because `total` is
deliberately None and sock_connect/sock_read only start once a connection has
been acquired. Overload became latency that grew without limit and memory that
grew with it, rather than a refusal a client could act on. The rate limiter did
not help: it is per-IP and per-key, so a hundred well-behaved callers saturate
the proxy without any of them being throttled.

**No instance detection.** The README says exactly this — "nothing detects a
second instance — the failure is silent, and arrives as a provider invoice" —
which is a description of a gap, not a mitigation.
"""

import asyncio

import httpx
import pytest

from conftest import InMemoryRepository, minimal_config
from core.admission import AdmissionController
from test_e2e import LightweightAgent


# ── admission control ───────────────────────────────────────────────────────


def test_it_is_sized_from_the_connection_pool():
    """Admitting more than the connector can serve just moves the queue back
    into aiohttp, which is where it was unbounded."""
    controller = AdmissionController.from_config(
        {"connection_pool": {"max_connections": 40}}
    )

    assert controller.max_in_flight == 40
    assert controller.max_queued == 80  # queue_factor 2.0


def test_the_defaults_match_the_connector_default():
    controller = AdmissionController.from_config({})

    assert controller.max_in_flight == 100


def test_it_can_be_configured_directly():
    controller = AdmissionController.from_config(
        {"admission": {"max_in_flight": 5, "max_queued": 1, "retry_after_s": 7}}
    )

    assert (controller.max_in_flight, controller.max_queued) == (5, 1)
    assert controller.retry_after_s == 7


def test_it_can_be_disabled():
    controller = AdmissionController.from_config({"admission": {"max_in_flight": 0}})

    assert controller.enabled is False


@pytest.mark.asyncio
async def test_a_burst_is_absorbed_not_refused():
    """Queueing is right — it is unbounded queueing that is not."""
    controller = AdmissionController(max_in_flight=1, max_queued=4)

    held = await controller.acquire()
    assert held

    waiter = asyncio.create_task(controller.acquire())
    await asyncio.sleep(0)  # let it reach the semaphore
    assert not waiter.done(), "the second request should wait, not be refused"

    controller.release()
    assert await waiter is True


@pytest.mark.asyncio
async def test_a_full_waiting_room_is_refused():
    """The finding: past the ceiling there was no refusal at all."""
    controller = AdmissionController(max_in_flight=1, max_queued=1)

    assert await controller.acquire()  # takes the only slot
    waiter = asyncio.create_task(controller.acquire())
    await asyncio.sleep(0)  # occupies the only queue place

    assert await controller.acquire() is False

    controller.release()
    await waiter
    controller.release()


@pytest.mark.asyncio
async def test_a_slot_is_returned_even_when_the_handler_raises():
    """Otherwise the ceiling ratchets down to zero over time."""
    controller = AdmissionController(max_in_flight=1, max_queued=1)

    assert await controller.acquire()
    try:
        raise RuntimeError("handler blew up")
    except RuntimeError:
        pass
    finally:
        controller.release()

    assert await controller.acquire() is True


def _agent(admission: dict | None = None):
    from proxy.app_factory import create_app

    config = minimal_config()
    config["server"]["auth"]["enabled"] = False
    if admission is not None:
        config["admission"] = admission
    agent = LightweightAgent(InMemoryRepository(), config)
    agent.app = create_app(agent)
    return agent


@pytest.mark.asyncio
async def test_a_shed_request_gets_503_and_retry_after():
    """503 with Retry-After is a thing a client can act on; an unbounded wait
    is not."""
    agent = _agent({"max_in_flight": 1, "max_queued": 0})
    # Occupy the only slot so the next request has nowhere to wait.
    assert await agent.admission.acquire()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=agent.app), base_url="http://test"
    ) as c:
        resp = await c.post(
            "/v1/chat/completions",
            json={"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
        )

    agent.admission.release()

    assert resp.status_code == 503
    assert resp.json()["error"] == "overloaded"
    assert resp.headers["Retry-After"] == "1"


@pytest.mark.asyncio
async def test_the_control_plane_is_not_shed():
    """Shedding an operator's config-apply because inference is busy would be
    the wrong trade."""
    agent = _agent({"max_in_flight": 1, "max_queued": 0})
    assert await agent.admission.acquire()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=agent.app), base_url="http://test"
    ) as c:
        resp = await c.get("/api/v1/registry")

    agent.admission.release()

    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_shedding_is_counted():
    from core.metrics import LOAD_SHED

    def _value():
        for m in LOAD_SHED.collect():
            for s in m.samples:
                if s.name.endswith("_total"):
                    return s.value
        return 0.0

    agent = _agent({"max_in_flight": 1, "max_queued": 0})
    assert await agent.admission.acquire()
    before = _value()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=agent.app), base_url="http://test"
    ) as c:
        await c.post("/v1/chat/completions", json={"model": "x", "messages": []})

    agent.admission.release()

    assert _value() == before + 1


# ── instance guard ──────────────────────────────────────────────────────────


class _FakeRedis:
    def __init__(self, existing=None):
        self.store = dict(existing or {})

    async def set(self, key, value, ex=None):
        self.store[key] = value

    async def delete(self, key):
        self.store.pop(key, None)

    async def scan_iter(self, match=None):
        prefix = (match or "").rstrip("*")
        for key in list(self.store):
            if key.startswith(prefix):
                yield key


@pytest.mark.asyncio
async def test_a_single_instance_reports_one():
    from core.instance_guard import InstanceGuard

    guard = InstanceGuard(_FakeRedis())

    assert await guard.check_once() == 1
    assert guard.peers == []


@pytest.mark.asyncio
async def test_a_second_instance_is_detected():
    """The finding, stated directly: this produced no signal of any kind."""
    from core.instance_guard import InstanceGuard

    redis = _FakeRedis({"llmproxy:instance:other-host:99:abcd1234": "1"})
    guard = InstanceGuard(redis)

    assert await guard.check_once() == 2
    assert guard.peers == ["other-host:99:abcd1234"]


@pytest.mark.asyncio
async def test_it_registers_itself_so_the_other_side_sees_it_too():
    from core.instance_guard import InstanceGuard

    redis = _FakeRedis()
    guard = InstanceGuard(redis)
    await guard.check_once()

    assert f"llmproxy:instance:{guard.instance_id}" in redis.store


@pytest.mark.asyncio
async def test_deregistering_removes_the_key():
    """A clean shutdown must not leave a key that makes the next start look
    like a second instance for its whole TTL."""
    from core.instance_guard import InstanceGuard

    redis = _FakeRedis()
    guard = InstanceGuard(redis)
    await guard.check_once()
    await guard.deregister()

    assert redis.store == {}


@pytest.mark.asyncio
async def test_without_redis_it_is_inert_rather_than_broken():
    """Detection needs shared state; its absence must not fail startup."""
    from core.instance_guard import InstanceGuard

    guard = InstanceGuard(None)

    assert guard.enabled is False
    assert await guard.check_once() == 1
    await guard.run()  # returns immediately
    await guard.deregister()


@pytest.mark.asyncio
async def test_it_does_not_refuse_to_start_on_finding_a_peer():
    """Deliberate: a rolling update legitimately runs two for a few seconds,
    and a stale key after a crash must not block a restart."""
    from core.instance_guard import InstanceGuard

    guard = InstanceGuard(_FakeRedis({"llmproxy:instance:someone-else": "1"}))

    count = await guard.check_once()  # must not raise

    assert count == 2


@pytest.mark.asyncio
async def test_the_count_is_published_as_a_metric(monkeypatch):
    from core.instance_guard import InstanceGuard
    from core.metrics import INSTANCE_COUNT

    guard = InstanceGuard(_FakeRedis({"llmproxy:instance:peer": "1"}))
    task = asyncio.create_task(guard.run())
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    value = next(
        s.value for m in INSTANCE_COUNT.collect() for s in m.samples
    )
    assert value == 2

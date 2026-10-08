"""Queued audit writes are bounded, and visible.

The chat route started a background task per request for its spend and audit rows;
all of them wait for the one lock the audit chain needs. With a store slower than the
request rate they piled up without limit and with no signal: memory grew with the
backlog, rows landed later and later, and a crash lost whatever was still waiting.
"""

import asyncio

import pytest
from prometheus_client import REGISTRY

from proxy import audit_backlog


class _Agent:
    def __init__(self, max_pending):
        self.config = {"audit": {"max_pending_writes": max_pending}}
        self.tasks = set()

    def _spawn_task(self, coro):
        task = asyncio.create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return task


@pytest.fixture(autouse=True)
def _reset():
    audit_backlog.reset_for_tests()
    yield
    audit_backlog.reset_for_tests()


def _gauge():
    return REGISTRY.get_sample_value("llm_proxy_audit_backlog")


async def test_below_the_limit_writes_stay_off_the_request_path():
    agent = _Agent(max_pending=5)
    gate = asyncio.Event()
    done = []

    async def write(i):
        await gate.wait()
        done.append(i)

    for i in range(3):
        await asyncio.wait_for(audit_backlog.submit(agent, write(i), route="chat"), 1)

    assert audit_backlog.pending() == 3 and _gauge() == 3  # queued, requests not held
    gate.set()
    await asyncio.gather(*agent.tasks)
    assert sorted(done) == [0, 1, 2]
    assert audit_backlog.pending() == 0 and _gauge() == 0


async def test_at_the_limit_the_request_waits_for_its_own_write():
    agent = _Agent(max_pending=2)
    gate = asyncio.Event()

    async def write():
        await gate.wait()

    before = REGISTRY.get_sample_value(
        "llm_proxy_audit_persistence_total", {"route": "chat", "outcome": "backpressure"}
    ) or 0

    await audit_backlog.submit(agent, write(), route="chat")
    await audit_backlog.submit(agent, write(), route="chat")
    assert audit_backlog.pending() == 2

    third = asyncio.create_task(audit_backlog.submit(agent, write(), route="chat"))
    await asyncio.sleep(0.05)
    assert not third.done(), "the third request should be held until its write finishes"
    assert audit_backlog.pending() == 3

    gate.set()
    await asyncio.wait_for(third, 1)
    await asyncio.gather(*agent.tasks)

    assert audit_backlog.pending() == 0
    after = REGISTRY.get_sample_value(
        "llm_proxy_audit_persistence_total", {"route": "chat", "outcome": "backpressure"}
    )
    assert after == before + 1


async def test_a_failing_write_is_logged_and_still_released(caplog):
    agent = _Agent(max_pending=5)

    async def boom():
        raise RuntimeError("store is down")

    with caplog.at_level("WARNING", logger="llmproxy.audit_backlog"):
        await audit_backlog.submit(agent, boom(), route="chat")
        await asyncio.gather(*agent.tasks, return_exceptions=True)

    assert audit_backlog.pending() == 0
    assert "store is down" in caplog.text


async def test_a_limit_of_zero_means_unbounded():
    agent = _Agent(max_pending=0)
    gate = asyncio.Event()

    async def write():
        await gate.wait()

    for _ in range(10):
        await asyncio.wait_for(audit_backlog.submit(agent, write(), route="chat"), 1)
    assert audit_backlog.pending() == 10
    gate.set()
    await asyncio.gather(*agent.tasks)

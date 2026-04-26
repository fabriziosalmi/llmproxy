"""Strategic priority #3 — Budget persistence E2E.

Tier 1 (the previous commit) closed the leaky paths so that streaming
and embeddings actually enqueue persistence. These tests prove the
**full pipeline** end-to-end:

   charge_and_persist
       → rotator._pending_writes queue
           → drain_pending_writes (the 0.25s background flush)
               → store.set_state
                   → survives restart (new rotator hydrates from store)

The test stub mirrors the orchestrator's relevant surface — async lock,
write queue, store ref, hydrate-on-init — so we exercise the same
plumbing the production agent uses without spinning up the full
ProxyOrchestrator (which pulls in 20+ subsystems).
"""

import asyncio
import datetime
import pytest

from proxy.background import drain_pending_writes
from proxy.budget import charge_and_persist, hydrate_daily_total

from tests.conftest import InMemoryRepository


class _StubAgent:
    """The minimum surface the budget pipeline touches, wired to a real
    InMemoryRepository so persistence + hydration is end-to-end."""

    def __init__(self, store: InMemoryRepository):
        self.store = store
        self._budget_lock = asyncio.Lock()
        self._pending_writes: asyncio.Queue = asyncio.Queue(maxsize=500)
        self.total_cost_today = 0.0
        self._budget_date = ""

    def enqueue_write(self, key, value):
        self._pending_writes.put_nowait((key, value))

    async def boot(self):
        """What the orchestrator's setup() does for the budget — minus
        every other subsystem."""
        self.total_cost_today, self._budget_date = await hydrate_daily_total(self.store)


# ── Charge → drain → store ────────────────────────────────────────


@pytest.mark.asyncio
async def test_streaming_style_charge_reaches_store():
    """A streaming finally block calls charge_and_persist; the flush loop
    drains the queue; the persisted value lands in the store."""
    store = InMemoryRepository()
    agent = _StubAgent(store)
    await agent.boot()

    # Simulate a streaming charge.
    await charge_and_persist(agent, agent._budget_lock, 1.50)

    # Background flush loop drains.
    await drain_pending_writes(agent)

    persisted = await store.get_state("budget:daily_total", 0.0)
    assert persisted == pytest.approx(1.50)


@pytest.mark.asyncio
async def test_multiple_charges_accumulate_in_store():
    """Sequential charges leave the highest snapshot in store. Each enqueue
    overwrites the prior value via set_state — the latest write wins, and
    each value is the running total at that moment."""
    store = InMemoryRepository()
    agent = _StubAgent(store)
    await agent.boot()

    await charge_and_persist(agent, agent._budget_lock, 0.30)
    await charge_and_persist(agent, agent._budget_lock, 0.20)
    await charge_and_persist(agent, agent._budget_lock, 0.50)

    await drain_pending_writes(agent)

    persisted = await store.get_state("budget:daily_total", 0.0)
    assert persisted == pytest.approx(1.00)


@pytest.mark.asyncio
async def test_charge_without_drain_is_unpersisted():
    """Negative-control: the bug we fixed in the prior commit was exactly
    this — charge without enqueue would have left the store empty. Now
    charge enqueues, but if the drain never runs (crash before flush),
    the store is still un-updated. Proves the test setup actually
    distinguishes 'enqueued' from 'persisted'."""
    store = InMemoryRepository()
    agent = _StubAgent(store)
    await agent.boot()

    await charge_and_persist(agent, agent._budget_lock, 5.00)
    # Intentionally NO drain.

    persisted = await store.get_state("budget:daily_total", 0.0)
    # Boot wrote 0.0; the unpersisted $5 is still only in the queue.
    assert persisted == 0.0
    assert agent._pending_writes.qsize() == 1


# ── Restart simulation ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_restart_restores_persisted_total():
    """The whole point of persistence: a fresh agent against the same
    store hydrates today's total back to whatever was last drained."""
    store = InMemoryRepository()

    # First "boot": charge $7.25, drain, simulate process death.
    agent1 = _StubAgent(store)
    await agent1.boot()
    await charge_and_persist(agent1, agent1._budget_lock, 7.25)
    await drain_pending_writes(agent1)
    del agent1  # process exit

    # Second "boot": fresh agent, same store.
    agent2 = _StubAgent(store)
    await agent2.boot()

    assert agent2.total_cost_today == pytest.approx(7.25)
    assert agent2._budget_date == datetime.date.today().isoformat()


@pytest.mark.asyncio
async def test_restart_after_unpersisted_charge_loses_spend():
    """Demonstrates the failure mode that motivated Tier 1: a charge that
    never reached the store (e.g. crash before drain) is GONE on restart.
    This is why charge_and_persist must enqueue every time, and why the
    flush interval is 0.25s in production (small loss window)."""
    store = InMemoryRepository()

    agent1 = _StubAgent(store)
    await agent1.boot()
    await charge_and_persist(agent1, agent1._budget_lock, 9.99)
    # No drain — simulate SIGKILL between enqueue and flush.

    agent2 = _StubAgent(store)
    await agent2.boot()

    # The $9.99 is gone from disk-state.
    assert agent2.total_cost_today == 0.0


# ── Daily rollover ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_yesterday_state_resets_to_zero_on_boot():
    """If the saved date is stale, hydration resets today's total. Stops
    cumulative spend from yesterday from leaking into today's budget gate."""
    store = InMemoryRepository()
    yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
    await store.set_state("budget:daily_date", yesterday)
    await store.set_state("budget:daily_total", 42.00)

    agent = _StubAgent(store)
    await agent.boot()

    assert agent.total_cost_today == 0.0
    assert agent._budget_date == datetime.date.today().isoformat()
    # And the store should be reset too — next process to boot sees today.
    assert await store.get_state("budget:daily_date") == datetime.date.today().isoformat()
    assert await store.get_state("budget:daily_total") == 0.0


@pytest.mark.asyncio
async def test_first_boot_initializes_today_state():
    """Empty store → boot sets today's date and a 0.0 total so the next
    boot finds them and treats it as the same day."""
    store = InMemoryRepository()
    agent = _StubAgent(store)
    await agent.boot()

    assert agent.total_cost_today == 0.0
    assert agent._budget_date == datetime.date.today().isoformat()
    assert await store.get_state("budget:daily_total") == 0.0


# ── Concurrency under realistic flush cadence ─────────────────────


@pytest.mark.asyncio
async def test_concurrent_charges_persist_correct_total():
    """100 concurrent charges of $0.10 against one rotator + one store →
    drain → restart. Total must be exactly $10. Without the lock, the
    classic read-modify-write race would lose increments and produce
    a smaller total."""
    store = InMemoryRepository()
    agent1 = _StubAgent(store)
    await agent1.boot()

    N = 100
    AMOUNT = 0.10
    await asyncio.gather(*[
        charge_and_persist(agent1, agent1._budget_lock, AMOUNT) for _ in range(N)
    ])
    await drain_pending_writes(agent1)

    agent2 = _StubAgent(store)
    await agent2.boot()
    assert agent2.total_cost_today == pytest.approx(N * AMOUNT)

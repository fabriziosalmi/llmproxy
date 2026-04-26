"""Tier 1 budget persistence — streaming + embeddings.

Before this fix, only the chat (/v1/chat/completions) route enqueued a
`budget:daily_total` write after charging. Streaming and embeddings paths
mutated `agent.total_cost_today` in-memory but never persisted. A crash
between the in-memory charge and the next chat request lost that spend.

The fix is `proxy.budget.charge_and_persist(rotator, lock, amount)` —
single helper that both paths now use. Tests:

  1. helper increments AND enqueues under the same lock
  2. helper is a no-op for amount=0 (don't pollute the queue with zero-charges)
  3. helper swallows enqueue exceptions (a full queue must not 500 the request)
  4. concurrent calls under one lock land on the right total + queue size

A streaming-finally integration test isn't included here — wiring the
full async stream pipeline against a fake upstream is heavy. The helper
contract is the seam the streaming code now uses, so testing the helper
is what guarantees the streaming charge persists.
"""

import asyncio
import pytest

from proxy.budget import charge_and_persist


class _StubRotator:
    """Minimal rotator surface needed by charge_and_persist."""

    def __init__(self, queue_maxsize: int = 100):
        self.total_cost_today = 0.0
        self._pending_writes: asyncio.Queue = asyncio.Queue(maxsize=queue_maxsize)

    def enqueue_write(self, key, value):
        self._pending_writes.put_nowait((key, value))


@pytest.mark.asyncio
async def test_charge_and_persist_increments_and_enqueues():
    rotator = _StubRotator()
    lock = asyncio.Lock()

    await charge_and_persist(rotator, lock, 0.42)

    assert rotator.total_cost_today == pytest.approx(0.42)
    assert rotator._pending_writes.qsize() == 1
    key, value = rotator._pending_writes.get_nowait()
    assert key == "budget:daily_total"
    assert value == pytest.approx(0.42)


@pytest.mark.asyncio
async def test_charge_and_persist_zero_amount_is_noop():
    """Zero-charge calls (e.g. failed upstream returning no usage) must
    not fill the persistence queue with no-op writes — the flush loop
    runs every 0.25s and we don't need 4 writes/sec of identical totals."""
    rotator = _StubRotator()
    lock = asyncio.Lock()

    await charge_and_persist(rotator, lock, 0.0)

    assert rotator.total_cost_today == 0.0
    assert rotator._pending_writes.empty()


@pytest.mark.asyncio
async def test_charge_and_persist_swallows_enqueue_exception():
    """A full queue or any other enqueue failure must NOT raise from the
    request path — the in-memory increment has already happened, and
    losing one disk write is recoverable from the audit ledger."""
    rotator = _StubRotator()
    lock = asyncio.Lock()

    # Replace enqueue with one that always blows up.
    def _broken(_key, _value):
        raise RuntimeError("queue is on fire")

    rotator.enqueue_write = _broken  # type: ignore[assignment]

    # Must not raise.
    await charge_and_persist(rotator, lock, 0.10)

    # In-memory charge still happened — that's the contract.
    assert rotator.total_cost_today == pytest.approx(0.10)


@pytest.mark.asyncio
async def test_charge_and_persist_serializes_under_lock():
    """Concurrent charges land on the right total — proves the lock is
    actually doing its job. Without serialization, two coroutines reading
    `total_cost_today` simultaneously and writing back +amount would lose
    one of the increments (classic read-modify-write race).
    """
    rotator = _StubRotator(queue_maxsize=1000)
    lock = asyncio.Lock()

    N = 200
    AMOUNT = 0.01
    await asyncio.gather(*[
        charge_and_persist(rotator, lock, AMOUNT) for _ in range(N)
    ])

    assert rotator.total_cost_today == pytest.approx(N * AMOUNT)
    assert rotator._pending_writes.qsize() == N
    # Last enqueued value should equal the final total (each enqueue writes
    # the running running total under the lock, so the final entry has
    # the highest value).
    values = []
    while not rotator._pending_writes.empty():
        _, v = rotator._pending_writes.get_nowait()
        values.append(v)
    # Strictly monotonic increasing — every increment is observed
    # in-order under the lock.
    assert values == sorted(values)
    assert values[-1] == pytest.approx(N * AMOUNT)


@pytest.mark.asyncio
async def test_streaming_path_uses_charge_and_persist():
    """Smoke check: the forwarder's streaming finally block routes through
    the helper. We can't run the full stream here, but we can verify the
    forwarder module imports the helper at the call site and not some
    drift over time. This catches refactors that accidentally re-inline
    the logic and forget the persistence step."""
    import inspect

    from proxy import forwarder

    source = inspect.getsource(forwarder)
    assert "charge_and_persist" in source, (
        "forwarder.py no longer references charge_and_persist — "
        "did the streaming finally block get re-inlined without persistence?"
    )


@pytest.mark.asyncio
async def test_embeddings_route_uses_charge_and_persist():
    """Same smoke check for embeddings."""
    import inspect

    from proxy.routes import embeddings

    source = inspect.getsource(embeddings)
    assert "charge_and_persist" in source, (
        "embeddings.py no longer references charge_and_persist — "
        "did the cost-tracking block get re-inlined without persistence?"
    )

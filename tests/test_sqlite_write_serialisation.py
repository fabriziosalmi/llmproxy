"""Every SQLite writer shares one lock, so audit appends are never lost.

SQLiteStore keeps a single aiosqlite connection. log_audit opened an explicit
``BEGIN IMMEDIATE`` on it while holding only the audit lock; log_spend,
set_state, add_endpoint and the other writers executed and committed on the same
connection without taking any lock. Interleaved at an ``await``, either:

* a writer's statement ran inside the audit transaction and its ``commit()``
  ended that transaction early (the audit insert then ran outside it), or
* log_audit's BEGIN found a transaction another writer had opened and failed
  with "cannot start a transaction within a transaction".

The audit request path swallows a store error with a warning, so the row was
simply gone, and verify_audit_chain stayed ``valid``: a missing row that was
never linked to leaves no break.
"""

import asyncio
import random
import time

import pytest

from models import EndpointStatus, LLMEndpoint
from store.sql_store import SQLiteStore


@pytest.fixture
async def store(tmp_path):
    s = SQLiteStore(str(tmp_path / "writes.db"))
    await s.init_db()
    yield s
    await s.close()


def _audit(store, i):
    return store.log_audit(
        ts=int(time.time()),
        req_id=f"req-{i}",
        session_id=f"sess-{i % 7}",
        key_prefix="sk-test",
        model="gpt-4o",
        provider="openai",
        status=200,
        prompt_tokens=1,
        completion_tokens=1,
        cost_usd=0.0,
        latency_ms=1.0,
    )


def _spend(store, i):
    return store.log_spend(
        ts=int(time.time()),
        date="2026-10-08",
        key_prefix="sk-test",
        model="gpt-4o",
        provider="openai",
        prompt_tokens=1,
        completion_tokens=1,
        cost_usd=0.0,
        latency_ms=1.0,
        status=200,
    )


async def _count(store, table):
    conn = await store._get_conn()
    async with conn.execute(f"SELECT COUNT(*) FROM {table}") as cur:
        return (await cur.fetchone())[0]


async def test_mixed_concurrent_writers_lose_no_audit_row(store):
    """Writers arrive spread over time, as requests do, not in one gather."""
    n = 300
    rng = random.Random(7)

    async def later(coro):
        await asyncio.sleep(rng.random() * 0.05)
        return await coro

    jobs = []
    for i in range(n):
        jobs.append(later(_audit(store, i)))
        jobs.append(later(_spend(store, i)))
        if i % 6 == 0:
            jobs.append(later(store.set_state(f"k{i}", {"i": i})))
        if i % 25 == 0:
            jobs.append(
                later(
                    store.add_endpoint(
                        LLMEndpoint(
                            id=f"ep-{i}",
                            url=f"http://ep{i}.example.com/v1",
                            status=EndpointStatus.VERIFIED,
                        )
                    )
                )
            )
    results = await asyncio.gather(*jobs, return_exceptions=True)

    assert [r for r in results if isinstance(r, BaseException)] == []
    assert await _count(store, "audit_log") == n
    assert await _count(store, "spend_log") == n
    verdict = await store.verify_audit_chain()
    assert verdict["valid"] is True
    assert verdict["verified"] == n


async def test_dangling_transaction_does_not_wedge_audit(store):
    """A writer cancelled between its statement and its commit leaves a
    transaction open on the shared connection. The next writer must recover
    from it, not fail with "cannot start a transaction within a transaction"
    on every request from then on."""
    conn = await store._get_conn()
    await conn.execute("INSERT INTO app_state (key, value) VALUES ('stray', '1')")
    assert conn.in_transaction

    await _audit(store, 0)

    assert await _count(store, "audit_log") == 1
    # The abandoned write was rolled back, not committed by someone else's commit.
    assert await store.get_state("stray") is None


async def test_audit_rows_survive_a_cancelled_neighbour(store):
    n = 80
    spenders = [asyncio.ensure_future(_spend(store, i)) for i in range(n)]
    audits = [asyncio.ensure_future(_audit(store, i)) for i in range(n)]
    await asyncio.sleep(0)
    for task in spenders[::3]:
        task.cancel()
    results = await asyncio.gather(*audits, return_exceptions=True)
    await asyncio.gather(*spenders, return_exceptions=True)

    assert [r for r in results if isinstance(r, BaseException)] == []
    assert await _count(store, "audit_log") == n
    assert (await store.verify_audit_chain())["valid"] is True

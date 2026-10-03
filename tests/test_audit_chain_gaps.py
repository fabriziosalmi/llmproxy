"""The audit chain stays verifiable across retention purge and erasure.

verify_audit_chain expected the first remaining row to link to GENESIS, so the
first retention purge (on by default after 90 days) made it report
``prev_hash mismatch`` on a perfectly healthy log, permanently. GDPR erasure
deletes rows from the middle of the chain with the same effect. A legitimate
deletion now leaves a gap record, and the verifier bridges exactly that break
and no other.

Runs against SQLite always, and against Postgres when TEST_POSTGRES_DSN is set
(CI sets it; ``make test-pg`` starts one).
"""

import os
import time

import pytest

from store import audit_chain
from store.sql_store import SQLiteStore

DAY = 86400
TEST_POSTGRES_DSN = os.environ.get("TEST_POSTGRES_DSN", "")


@pytest.fixture(params=["sqlite", "postgres"])
async def store(request, tmp_path):
    if request.param == "sqlite":
        s = SQLiteStore(str(tmp_path / "audit.db"))
        await s.init_db()
        yield s
        await s.close()
        return

    if not TEST_POSTGRES_DSN:
        pytest.skip("TEST_POSTGRES_DSN not set")
    asyncpg = pytest.importorskip("asyncpg")
    from store.pg_store import PostgresStore

    raw = await asyncpg.connect(TEST_POSTGRES_DSN)
    try:
        await raw.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    finally:
        await raw.close()
    s = PostgresStore(TEST_POSTGRES_DSN)
    await s.init_db()
    yield s
    await s.close()


async def _log(store, age_days, req_id, session="sess", key="sk-test"):
    await store.log_audit(
        ts=int(time.time()) - int(age_days * DAY),
        req_id=req_id,
        session_id=session,
        key_prefix=key,
        model="gpt-4o",
        provider="openai",
        status=200,
        prompt_tokens=1,
        completion_tokens=1,
        cost_usd=0.0,
        latency_ms=1.0,
    )


async def _rows(store):
    """(id, req_id) for every surviving audit row, oldest first."""
    if hasattr(store, "_get_conn"):
        conn = await store._get_conn()
        async with conn.execute("SELECT id, req_id FROM audit_log ORDER BY id") as cur:
            return [tuple(r) for r in await cur.fetchall()]
    pool = await store.init_pool()
    return [tuple(r) for r in await pool.fetch("SELECT id, req_id FROM audit_log ORDER BY id")]


async def _execute(store, sql_sqlite, sql_pg, *args):
    if hasattr(store, "_get_conn"):
        conn = await store._get_conn()
        await conn.execute(sql_sqlite, args)
        await conn.commit()
    else:
        pool = await store.init_pool()
        await pool.execute(sql_pg, *args)


# ── retention purge ─────────────────────────────────────────────────────────


async def test_chain_is_still_valid_after_a_retention_purge(store):
    for i, age in enumerate([200, 150, 10, 5]):
        await _log(store, age, f"r{i}")
    assert (await store.verify_audit_chain())["valid"] is True

    result = await store.purge_expired(90)
    assert result["audit_deleted"] == 2

    verdict = await store.verify_audit_chain()
    assert verdict["valid"] is True, verdict
    assert verdict["verified"] == 2
    assert verdict["rows_removed"] == 2


async def test_successive_purges_stay_valid_and_collapse_to_one_record(store):
    for i, age in enumerate([300, 250, 200, 150, 10]):
        await _log(store, age, f"r{i}")

    await store.purge_expired(240)  # removes the 300- and 250-day-old rows
    assert (await store.verify_audit_chain())["valid"] is True
    await store.purge_expired(90)  # removes the next two

    verdict = await store.verify_audit_chain()
    assert verdict["valid"] is True, verdict
    assert verdict["verified"] == 1
    assert verdict["rows_removed"] == 4
    gaps = audit_chain.load_gaps(await store.get_state(audit_chain.GAPS_KEY))
    assert len(gaps) == 1 and gaps[0]["rows"] == 4


async def test_appending_after_a_purge_keeps_the_chain_valid(store):
    for i, age in enumerate([200, 10]):
        await _log(store, age, f"r{i}")
    await store.purge_expired(90)
    await _log(store, 0, "fresh")

    verdict = await store.verify_audit_chain()
    assert verdict["valid"] is True, verdict
    assert verdict["verified"] == 2


async def test_purging_the_whole_chain_starts_a_new_one(store):
    for i, age in enumerate([200, 150]):
        await _log(store, age, f"r{i}")
    await store.purge_expired(90)
    assert await _rows(store) == []

    await _log(store, 0, "fresh")
    verdict = await store.verify_audit_chain()
    assert verdict["valid"] is True, verdict
    assert verdict["verified"] == 1


async def test_purge_with_nothing_expired_changes_nothing(store):
    await _log(store, 1, "r0")
    assert (await store.purge_expired(90))["audit_deleted"] == 0
    assert audit_chain.load_gaps(await store.get_state(audit_chain.GAPS_KEY)) == []
    assert (await store.verify_audit_chain())["rows_removed"] == 0


# ── GDPR erasure ────────────────────────────────────────────────────────────


async def test_chain_is_still_valid_after_erasing_a_subject_mid_chain(store):
    await _log(store, 5, "a1", session="alice")
    await _log(store, 4, "b1", session="bob")
    await _log(store, 3, "b2", session="bob")
    await _log(store, 2, "a2", session="alice")
    await _log(store, 1, "c1", session="carol")

    result = await store.delete_subject_data("bob")
    assert result["audit_deleted"] == 2

    verdict = await store.verify_audit_chain()
    assert verdict["valid"] is True, verdict
    assert verdict["verified"] == 3
    assert verdict["rows_removed"] == 2


async def test_erasing_non_adjacent_rows_records_each_run(store):
    for i, who in enumerate(["bob", "alice", "bob", "alice", "bob", "alice"]):
        await _log(store, 6 - i, f"r{i}", session=who)

    await store.delete_subject_data("bob")

    verdict = await store.verify_audit_chain()
    assert verdict["valid"] is True, verdict
    assert verdict["verified"] == 3
    assert verdict["rows_removed"] == 3  # three separate runs, each bridged


async def test_erasing_the_newest_rows_then_appending_stays_valid(store):
    await _log(store, 3, "a1", session="alice")
    await _log(store, 2, "b1", session="bob")
    await store.delete_subject_data("bob")
    await _log(store, 0, "a2", session="alice")

    verdict = await store.verify_audit_chain()
    assert verdict["valid"] is True, verdict
    assert verdict["verified"] == 2


# ── a deletion that was not recorded is still tampering ─────────────────────


async def test_an_unrecorded_deletion_still_breaks_the_chain(store):
    for i in range(4):
        await _log(store, 4 - i, f"r{i}")
    middle = (await _rows(store))[1][0]

    await _execute(
        store,
        "DELETE FROM audit_log WHERE id = ?",
        "DELETE FROM audit_log WHERE id = $1",
        middle,
    )

    verdict = await store.verify_audit_chain()
    assert verdict["valid"] is False
    assert "prev_hash mismatch" in verdict["error"]


async def test_a_recorded_gap_does_not_excuse_a_different_deletion(store):
    for i, age in enumerate([200, 10, 9, 8, 7]):
        await _log(store, age, f"r{i}")
    await store.purge_expired(90)  # legitimate: removes the 200-day-old row
    assert (await store.verify_audit_chain())["valid"] is True

    rows = await _rows(store)
    await _execute(
        store,
        "DELETE FROM audit_log WHERE id = ?",
        "DELETE FROM audit_log WHERE id = $1",
        rows[1][0],
    )

    assert (await store.verify_audit_chain())["valid"] is False


async def test_editing_a_row_after_a_purge_is_still_detected(store):
    for i, age in enumerate([200, 10, 9]):
        await _log(store, age, f"r{i}")
    await store.purge_expired(90)
    survivor = (await _rows(store))[0][0]

    await _execute(
        store,
        "UPDATE audit_log SET cost_usd = 99 WHERE id = ?",
        "UPDATE audit_log SET cost_usd = 99 WHERE id = $1",
        survivor,
    )

    verdict = await store.verify_audit_chain()
    assert verdict["valid"] is False
    assert "entry_hash mismatch" in verdict["error"]


async def test_a_blank_hash_after_hashed_rows_is_a_break_on_both_backends(store):
    # The Postgres verifier used to treat this as a reset to GENESIS.
    for i in range(3):
        await _log(store, 3 - i, f"r{i}")
    last = (await _rows(store))[-1][0]

    await _execute(
        store,
        "UPDATE audit_log SET entry_hash = '' WHERE id = ?",
        "UPDATE audit_log SET entry_hash = '' WHERE id = $1",
        last,
    )

    verdict = await store.verify_audit_chain()
    assert verdict["valid"] is False
    assert "blank entry_hash" in verdict["error"]


# ── the pure pieces ─────────────────────────────────────────────────────────


def _row(i, prev, end):
    return {"id": i, "prev_hash": prev, "entry_hash": end}


def test_adjacent_rows_become_one_segment_and_legacy_rows_are_skipped():
    removed = [
        _row(1, "", ""),  # legacy, pre-chain
        _row(2, "GENESIS", "h2"),
        _row(3, "h2", "h3"),
        _row(5, "h4", "h5"),  # not adjacent to 3: h4 survives
    ]
    segments = audit_chain.segments_from_rows(removed, reason="erasure", now=1)

    assert [(s["start"], s["end"], s["rows"]) for s in segments] == [
        ("GENESIS", "h3", 2),
        ("h4", "h5", 1),
    ]


def test_merge_joins_segments_that_run_end_to_start_in_any_order():
    a = {"start": "g", "end": "h2", "rows": 2, "reason": "retention", "at": 1}
    b = {"start": "h2", "end": "h5", "rows": 3, "reason": "retention", "at": 2}
    c = {"start": "h5", "end": "h6", "rows": 1, "reason": "erasure", "at": 3}

    merged = audit_chain.merge_gaps([c], [b, a])

    assert len(merged) == 1
    assert (merged[0]["start"], merged[0]["end"], merged[0]["rows"]) == ("g", "h6", 6)
    assert merged[0]["reason"] == "mixed"


def test_load_gaps_tolerates_missing_and_malformed_state():
    assert audit_chain.load_gaps(None) == []
    assert audit_chain.load_gaps("not json") == []
    assert audit_chain.load_gaps([{"start": "a"}, "junk", {"start": "a", "end": "b"}]) == [
        {"start": "a", "end": "b"}
    ]


# ── the whole chain is verified, page by page ───────────────────────────────


@pytest.fixture
def small_pages(monkeypatch):
    """Force several pages so the cursor and the carried state are exercised."""
    monkeypatch.setattr(audit_chain, "VERIFY_PAGE_SIZE", 3)


async def test_a_long_chain_verifies_across_page_boundaries(store, small_pages):
    for i in range(10):
        await _log(store, 10 - i, f"r{i}")

    verdict = await store.verify_audit_chain()

    assert verdict["valid"] is True, verdict
    assert verdict["total"] == 10 and verdict["verified"] == 10


async def test_tampering_beyond_the_first_page_is_detected(store, small_pages):
    # The old verifier read only the first N rows by id and called the rest valid.
    for i in range(10):
        await _log(store, 10 - i, f"r{i}")
    victim = (await _rows(store))[8][0]  # the ninth row: pages of 3 reach it in page 3

    await _execute(
        store,
        "UPDATE audit_log SET cost_usd = 99 WHERE id = ?",
        "UPDATE audit_log SET cost_usd = 99 WHERE id = $1",
        victim,
    )

    verdict = await store.verify_audit_chain()
    assert verdict["valid"] is False
    assert verdict["broken_at"] == victim
    assert verdict["total"] == 9  # rows examined up to and including the failure


async def test_a_gap_bridge_works_when_the_break_falls_on_a_page_boundary(
    store, small_pages
):
    for i, age in enumerate([200, 190, 180, 5, 4, 3, 2, 1]):
        await _log(store, age, f"r{i}")
    await store.purge_expired(90)  # removes the first three; survivors start a page

    verdict = await store.verify_audit_chain()

    assert verdict["valid"] is True, verdict
    assert verdict["verified"] == 5 and verdict["rows_removed"] == 3


async def test_rows_appended_between_pages_do_not_break_verification(store, small_pages):
    for i in range(6):
        await _log(store, 6 - i, f"r{i}")
    await _log(store, 0, "late")

    assert (await store.verify_audit_chain())["verified"] == 7


def test_the_verifier_carries_state_between_feeds_like_one_call():
    rows = []
    prev = "GENESIS"
    for i in range(1, 8):
        row = {
            "id": i, "ts": i, "req_id": f"r{i}", "session_id": "s", "key_prefix": "k",
            "model": "m", "provider": "p", "status": 200, "prompt_tokens": 1,
            "completion_tokens": 1, "cost_usd": 0.0, "latency_ms": 1.0, "blocked": 0,
            "block_reason": "", "metadata": "{}", "prev_hash": prev,
        }
        row["entry_hash"] = audit_chain.entry_hash(prev, row)
        prev = row["entry_hash"]
        rows.append(row)

    paged = audit_chain.ChainVerifier()
    for i in range(0, len(rows), 2):
        assert paged.feed(rows[i : i + 2]) is None

    assert paged.result() == audit_chain.verify_rows(rows)
    assert paged.result()["verified"] == 7

    rows[4]["cost_usd"] = 1.0
    bad = audit_chain.ChainVerifier()
    failure = None
    for i in range(0, len(rows), 2):  # the stores stop at the first failure
        failure = bad.feed(rows[i : i + 2])
        if failure:
            break
    assert failure["broken_at"] == 5
    assert failure["total"] == 5


async def test_tampering_past_one_hundred_thousand_rows_is_detected(tmp_path):
    """The reported defect itself: the old verifier stopped at 100,000 rows."""
    s = SQLiteStore(str(tmp_path / "big.db"))
    await s.init_db()
    n = 100_050
    base = {
        "session_id": "s", "key_prefix": "k", "model": "m", "provider": "p",
        "status": 200, "prompt_tokens": 1, "completion_tokens": 1, "cost_usd": 0.0,
        "latency_ms": 1.0, "blocked": 0, "block_reason": "", "metadata": "{}",
    }
    prev, batch = "GENESIS", []
    for i in range(1, n + 1):
        row = {**base, "ts": i, "req_id": f"r{i}"}
        h = audit_chain.entry_hash(prev, row)
        batch.append(
            (
                i, row["ts"], row["req_id"], "s", "k", "m", "p", 200, 1, 1, 0.0, 1.0,
                0, "", "{}", h, prev,
            )
        )
        prev = h
    conn = await s._get_conn()
    await conn.executemany(
        "INSERT INTO audit_log (id, ts, req_id, session_id, key_prefix, model, provider, "
        "status, prompt_tokens, completion_tokens, cost_usd, latency_ms, blocked, "
        "block_reason, metadata, entry_hash, prev_hash) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        batch,
    )
    await conn.commit()

    healthy = await s.verify_audit_chain()
    assert healthy["valid"] is True and healthy["verified"] == n

    await conn.execute("UPDATE audit_log SET cost_usd = 99 WHERE id = ?", (n - 3,))
    await conn.commit()

    verdict = await s.verify_audit_chain()
    assert verdict["valid"] is False
    assert verdict["broken_at"] == n - 3
    await s.close()

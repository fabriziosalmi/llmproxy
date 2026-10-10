"""An audit chain head recorded outside the database catches what the chain cannot.

The chain is keyless SHA-256, so someone who can write the database can edit a row
and recompute every later hash, or delete the newest rows, and verification still
passes against itself. Only a head recorded elsewhere reveals that. The head is
exported (GET /api/v1/audit/head, and an hourly AUDIT HEAD security log line that
reaches the SIEM), and /api/v1/audit/verify?anchor_id=&anchor_hash= checks the chain
against a recorded one. It does not add a key: it is the external copy the chain
needs, with no change to what is stored.
"""

import asyncio
import os
import time
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import FastAPI

from store import audit_chain
from store.sql_store import SQLiteStore
from tests.conftest import minimal_config

DAY = 86400
TEST_POSTGRES_DSN = os.environ.get("TEST_POSTGRES_DSN", "")


@pytest.fixture(params=["sqlite", "postgres"])
async def store(request, tmp_path):
    if request.param == "sqlite":
        s = SQLiteStore(str(tmp_path / "anchor.db"))
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


async def _log(store, age_days, req_id, session="s"):
    await store.log_audit(
        ts=int(time.time()) - int(age_days * DAY), req_id=req_id, session_id=session,
        key_prefix="k", model="m", provider="p", status=200, prompt_tokens=1,
        completion_tokens=1, cost_usd=0.5, latency_ms=1.0,
    )


async def _fill(store, n=6):
    for i in range(n):
        await _log(store, n - i, f"r{i}")


async def _all_rows(store):
    if hasattr(store, "_get_conn"):
        import aiosqlite

        conn = await store._get_conn()
        conn.row_factory = aiosqlite.Row
        try:
            async with conn.execute("SELECT * FROM audit_log ORDER BY id") as cur:
                return [dict(r) for r in await cur.fetchall()]
        finally:
            conn.row_factory = None
    pool = await store.init_pool()
    return [dict(r) for r in await pool.fetch("SELECT * FROM audit_log ORDER BY id")]


async def _exec(store, sql_sqlite, sql_pg, *args):
    if hasattr(store, "_get_conn"):
        conn = await store._get_conn()
        await conn.execute(sql_sqlite, args)
        await conn.commit()
    else:
        pool = await store.init_pool()
        await pool.execute(sql_pg, *args)


async def _rewrite_and_rechain(store, row_id):
    """What someone with write access can do: edit a row, then fix every hash after it."""
    rows = await _all_rows(store)
    prev = next(r["prev_hash"] for r in rows if r["id"] == row_id)
    for row in rows:
        if row["id"] < row_id:
            continue
        if row["id"] == row_id:
            row["cost_usd"] = 0.0  # the edit
        row["prev_hash"] = prev
        row["entry_hash"] = audit_chain.entry_hash(prev, row, audit_chain.row_version(row))
        prev = row["entry_hash"]
        await _exec(
            store,
            "UPDATE audit_log SET cost_usd = ?, prev_hash = ?, entry_hash = ? WHERE id = ?",
            "UPDATE audit_log SET cost_usd = $1, prev_hash = $2, entry_hash = $3 WHERE id = $4",
            row["cost_usd"], row["prev_hash"], row["entry_hash"], row["id"],
        )


# ── the head ────────────────────────────────────────────────────────────────


async def test_the_head_is_the_newest_row_and_the_count(store):
    assert await store.get_audit_head() == {"id": 0, "hash": "GENESIS", "count": 0}
    await _fill(store, 4)

    head = await store.get_audit_head()
    last = (await _all_rows(store))[-1]

    assert head == {"id": last["id"], "hash": last["entry_hash"], "count": 4}


# ── the attack the chain alone cannot see ───────────────────────────────────


async def test_a_rewrite_that_recomputes_the_chain_passes_without_an_anchor(store):
    await _fill(store)
    await _rewrite_and_rechain(store, row_id=3)

    assert (await store.verify_audit_chain())["valid"] is True  # the limitation, stated


async def test_the_same_rewrite_is_caught_by_a_head_recorded_beforehand(store):
    await _fill(store)
    head = await store.get_audit_head()
    anchor = {"id": head["id"], "hash": head["hash"]}
    await _rewrite_and_rechain(store, row_id=3)

    verdict = await store.verify_audit_chain(anchor)

    assert verdict["valid"] is False
    assert verdict["anchor"]["status"] == "mismatch"
    assert "rewritten" in verdict["error"] and verdict["broken_at"] == head["id"]


async def test_deleting_the_newest_rows_passes_alone_and_fails_against_a_head(store):
    await _fill(store)
    head = await store.get_audit_head()
    await _exec(store, "DELETE FROM audit_log WHERE id > ?", "DELETE FROM audit_log WHERE id > $1", head["id"] - 2)

    assert (await store.verify_audit_chain())["valid"] is True
    verdict = await store.verify_audit_chain({"id": head["id"], "hash": head["hash"]})

    assert verdict["valid"] is False
    assert verdict["anchor"]["status"] == "truncated"
    assert "truncated" in verdict["error"]


async def test_an_untouched_chain_matches_its_own_recorded_head(store):
    await _fill(store)
    head = await store.get_audit_head()

    verdict = await store.verify_audit_chain({"id": head["id"], "hash": head["hash"]})

    assert verdict["valid"] is True
    assert verdict["anchor"] == {"id": head["id"], "status": "ok", "rows_removed_since": 0}


async def test_an_older_head_still_matches_after_more_rows_are_appended(store):
    await _fill(store, 4)
    head = await store.get_audit_head()
    await _log(store, 0, "later")

    verdict = await store.verify_audit_chain({"id": head["id"], "hash": head["hash"]})

    assert verdict["valid"] is True and verdict["anchor"]["status"] == "ok"


# ── legitimate deletions do not trip an anchor ──────────────────────────────


async def test_a_head_older_than_the_retained_chain_is_reported_purged_not_broken(store):
    for i, age in enumerate([200, 190, 10, 5]):
        await _log(store, age, f"r{i}")
    rows = await _all_rows(store)
    old = {"id": rows[0]["id"], "hash": rows[0]["entry_hash"]}
    await store.purge_expired(90)

    verdict = await store.verify_audit_chain(old)

    assert verdict["valid"] is True and verdict["anchor"]["status"] == "purged"


async def test_an_anchored_row_removed_by_a_recorded_erasure_is_not_a_break(store):
    await _log(store, 4, "a", session="alice-session-1")
    await _log(store, 3, "b", session="bob-session-22")
    await _log(store, 2, "c", session="alice-session-1")
    bob = next(r for r in await _all_rows(store) if r["req_id"] == "b")
    await store.delete_subject_data("bob-session-22")

    verdict = await store.verify_audit_chain({"id": bob["id"], "hash": bob["entry_hash"]})

    assert verdict["valid"] is True and verdict["anchor"]["status"] == "erased"


async def test_an_anchored_row_missing_without_a_recorded_deletion_is_a_break(store):
    await _fill(store, 5)
    rows = await _all_rows(store)
    victim = rows[2]
    await _exec(store, "DELETE FROM audit_log WHERE id = ?", "DELETE FROM audit_log WHERE id = $1", victim["id"])

    verdict = await store.verify_audit_chain({"id": victim["id"], "hash": victim["entry_hash"]})

    assert verdict["valid"] is False


# ── parsing what an operator pastes ─────────────────────────────────────────


def test_parse_anchor_accepts_a_good_head_and_none():
    digest = "ab" * 32
    assert audit_chain.parse_anchor(None, None) is None
    assert audit_chain.parse_anchor("7", digest.upper()) == {"id": 7, "hash": digest}


@pytest.mark.parametrize(
    "anchor_id,anchor_hash",
    [("7", None), (None, "ab" * 32), ("x", "ab" * 32), ("0", "ab" * 32),
     ("-3", "ab" * 32), ("7", "short"), ("7", "zz" * 32), ("7", "ab" * 33)],
)
def test_parse_anchor_rejects_a_malformed_head(anchor_id, anchor_hash):
    with pytest.raises(ValueError):
        audit_chain.parse_anchor(anchor_id, anchor_hash)


# ── the routes ──────────────────────────────────────────────────────────────


class _FakeStore:
    def __init__(self):
        self.verified_with = "unset"

    async def verify_audit_chain(self, anchor=None):
        self.verified_with = anchor
        return {"valid": True, "total": 0, "verified": 0, "broken_at": None}

    async def get_audit_head(self):
        return {"id": 9, "hash": "ab" * 32, "count": 9}


def _app():
    from proxy.routes.admin import create_router

    agent = MagicMock()
    agent.config = minimal_config(auth_enabled=False)
    agent.store = _FakeStore()
    app = FastAPI()
    app.include_router(create_router(agent))
    return agent, app


async def _get(app, path):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        return await c.get(path)


async def test_the_head_route_returns_the_head():
    _, app = _app()
    resp = await _get(app, "/api/v1/audit/head")
    assert resp.json() == {"id": 9, "hash": "ab" * 32, "count": 9}


async def test_verify_without_an_anchor_behaves_as_before():
    agent, app = _app()
    resp = await _get(app, "/api/v1/audit/verify")
    assert resp.status_code == 200 and agent.store.verified_with is None


async def test_verify_passes_a_valid_anchor_to_the_store():
    agent, app = _app()
    resp = await _get(app, f"/api/v1/audit/verify?anchor_id=9&anchor_hash={'ab' * 32}")
    assert resp.status_code == 200
    assert agent.store.verified_with == {"id": 9, "hash": "ab" * 32}


@pytest.mark.parametrize("query", [
    "anchor_id=9", f"anchor_hash={'ab' * 32}", "anchor_id=x&anchor_hash=" + "ab" * 32,
    "anchor_id=9&anchor_hash=nothex",
])
async def test_verify_rejects_a_malformed_anchor_with_400(query):
    _, app = _app()
    resp = await _get(app, f"/api/v1/audit/verify?{query}")
    assert resp.status_code == 400


# ── publishing the head ─────────────────────────────────────────────────────


class _Stop(Exception):
    pass


async def _run_one_iteration(agent, interval=3600):
    from proxy import background

    async def stop(_):
        raise _Stop

    original = background.asyncio.sleep
    background.asyncio.sleep = stop
    try:
        with pytest.raises(_Stop):
            await background.audit_head_loop(agent, interval)
    finally:
        background.asyncio.sleep = original


async def test_the_loop_publishes_the_head_to_the_security_log():
    agent = MagicMock()
    agent.store.get_audit_head = AsyncMock(return_value={"id": 12, "hash": "cd" * 32, "count": 12})
    agent._add_log = AsyncMock()

    await _run_one_iteration(agent)

    agent._add_log.assert_awaited_once_with(
        f"AUDIT HEAD id=12 hash={'cd' * 32} count=12", level="SECURITY"
    )


async def test_the_loop_stays_quiet_on_an_empty_chain():
    agent = MagicMock()
    agent.store.get_audit_head = AsyncMock(return_value={"id": 0, "hash": "GENESIS", "count": 0})
    agent._add_log = AsyncMock()

    await _run_one_iteration(agent)

    agent._add_log.assert_not_awaited()


async def test_a_failure_does_not_stop_the_loop():
    agent = MagicMock()
    agent.store.get_audit_head = AsyncMock(side_effect=RuntimeError("db down"))
    agent._add_log = AsyncMock()

    await _run_one_iteration(agent)  # reaches the sleep, i.e. survived the error


async def test_an_interval_of_zero_turns_it_off():
    from proxy import background

    agent = MagicMock()
    agent.store.get_audit_head = AsyncMock()

    await asyncio.wait_for(background.audit_head_loop(agent, 0), timeout=1)

    agent.store.get_audit_head.assert_not_called()


def test_the_interval_is_validated_as_a_number():
    from core.startup_checks import StartupError, validate_config

    cfg = minimal_config()
    cfg["server"]["auth"]["enabled"] = False
    validate_config({**cfg, "audit": {"head_log_interval_seconds": 0}})
    with pytest.raises(StartupError, match="audit.head_log_interval_seconds"):
        validate_config({**cfg, "audit": {"head_log_interval_seconds": "3600"}})

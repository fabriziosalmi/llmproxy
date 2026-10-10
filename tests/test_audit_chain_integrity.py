"""What the audit chain proves, and against whom.

Three defects in the chain as it was:

* A removal record lived in ``app_state``, next to the table it vouched for and
  covered by no hash. Whoever could delete a row could write the record that
  excused it, and the verifier answered ``valid`` even against a head recorded
  outside the database. Removal records are now rows of the chain.
* The hash preimage joined fields with ``|``, so two different rows could share
  one. Rows are now sealed over a canonical encoding.
* Nothing separated someone who can write the database from the proxy itself.
  With ``LLM_PROXY_AUDIT_KEY`` the chain is keyed, and a writer of the database
  who does not hold the key cannot alter, remove or append rows unnoticed.

Runs against SQLite always, and against Postgres when TEST_POSTGRES_DSN is set.
"""

import json
import os
import time

import pytest

from store import audit_chain
from store.sql_store import SQLiteStore

DAY = 86400
TEST_POSTGRES_DSN = os.environ.get("TEST_POSTGRES_DSN", "")
KEY_A = "a" * 40
KEY_B = "b" * 40


@pytest.fixture(params=["sqlite", "postgres"])
async def open_store(request, tmp_path, monkeypatch):
    """``await open_store(key=None, previous=None)``: a store on one shared database.

    Called again it reopens the same database, as a restart with a different
    environment would.
    """
    opened = []
    monkeypatch.delenv(audit_chain.KEY_ENV, raising=False)
    monkeypatch.delenv(audit_chain.PREVIOUS_KEYS_ENV, raising=False)

    if request.param == "postgres":
        if not TEST_POSTGRES_DSN:
            pytest.skip("TEST_POSTGRES_DSN not set")
        asyncpg = pytest.importorskip("asyncpg")
        raw = await asyncpg.connect(TEST_POSTGRES_DSN)
        try:
            await raw.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        finally:
            await raw.close()

    async def _open(key=None, previous=None):
        for name, value in ((audit_chain.KEY_ENV, key), (audit_chain.PREVIOUS_KEYS_ENV, previous)):
            if value:
                monkeypatch.setenv(name, value)
            else:
                monkeypatch.delenv(name, raising=False)
        if request.param == "sqlite":
            s = SQLiteStore(str(tmp_path / "audit.db"))
        else:
            from store.pg_store import PostgresStore

            s = PostgresStore(TEST_POSTGRES_DSN)
        await s.init_db()
        opened.append(s)
        return s

    yield _open
    for s in opened:
        await s.close()


async def _log(store, age_days, req_id, session="sess", key="sk-test", **over):
    fields = dict(
        ts=int(time.time()) - int(age_days * DAY),
        req_id=req_id,
        session_id=session,
        key_prefix=key,
        model="gpt-4o",
        provider="openai",
        status=200,
        prompt_tokens=1,
        completion_tokens=1,
        cost_usd=0.5,
        latency_ms=1.0,
    )
    fields.update(over)
    await store.log_audit(**fields)


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


async def _exec(store, sql, *args):
    """Run ``sql`` (written with ``?`` placeholders) as someone with database access."""
    if hasattr(store, "_get_conn"):
        conn = await store._get_conn()
        await conn.execute(sql, args)
        await conn.commit()
        return
    numbered, n = "", 0
    for ch in sql:
        if ch == "?":
            n += 1
            numbered += f"${n}"
        else:
            numbered += ch
    pool = await store.init_pool()
    await pool.execute(numbered, *args)


async def _insert_raw(store, row, version, prev, key=None):
    """Append a row the way an attacker (or an old release) would: straight SQL."""
    digest = audit_chain.entry_hash(prev, row, version, key)
    await _exec(
        store,
        "INSERT INTO audit_log (ts, req_id, session_id, key_prefix, model, provider, status, "
        "prompt_tokens, completion_tokens, cost_usd, latency_ms, blocked, block_reason, "
        "metadata, entry_hash, prev_hash, chain_v) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        row["ts"], row["req_id"], row["session_id"], row["key_prefix"], row["model"],
        row["provider"], row["status"], row["prompt_tokens"], row["completion_tokens"],
        row["cost_usd"], row["latency_ms"], row["blocked"], row["block_reason"],
        row["metadata"], digest, prev, version,
    )
    return digest


def _forged_record(rows, first, last):
    """The removal record for rows[first..last], as the proxy itself would write it."""
    metadata = audit_chain.removal_metadata(
        [
            {
                "start": rows[first]["prev_hash"],
                "end": rows[last]["entry_hash"],
                "rows": last - first + 1,
            }
        ],
        reason="retention",
    )[0]
    return audit_chain.removal_row(metadata, reason="retention")


# ── a record outside the chain excuses nothing ──────────────────────────────


async def test_a_record_written_to_app_state_does_not_excuse_a_deletion(open_store):
    store = await open_store()
    for i in range(6):
        await _log(store, 6 - i, f"r{i}")
    rows = await _all_rows(store)
    head = await store.get_audit_head()

    # Delete two rows from the middle, then write the record that used to make
    # the verifier bridge the hole.
    await _exec(store, "DELETE FROM audit_log WHERE id IN (?, ?)", rows[2]["id"], rows[3]["id"])
    forged = [
        {
            "start": rows[2]["prev_hash"],
            "end": rows[3]["entry_hash"],
            "rows": 2,
            "reason": "retention",
            "at": 0,
        }
    ]
    await store.set_state(audit_chain.GAPS_KEY, forged)

    verdict = await store.verify_audit_chain({"id": head["id"], "hash": head["hash"]})

    assert verdict["valid"] is False
    assert "prev_hash mismatch" in verdict["error"]
    assert verdict["broken_at"] == rows[4]["id"]


def test_a_record_that_is_not_a_verified_row_is_refused():
    """The same rule in the pure verifier: a record handed over from outside the
    chain bridges nothing on its own."""
    rows, prev = [], audit_chain.GENESIS
    for i in range(1, 7):
        row = audit_chain.normalise({"ts": i, "req_id": f"r{i}", "session_id": "s"})
        row.update(id=i, prev_hash=prev, chain_v=audit_chain.CHAIN_V2)
        row["entry_hash"] = prev = audit_chain.entry_hash(row["prev_hash"], row, audit_chain.CHAIN_V2)
        rows.append(row)
    kept = rows[:2] + rows[4:]
    outside = [{"start": rows[2]["prev_hash"], "end": rows[3]["entry_hash"], "rows": 2}]

    verdict = audit_chain.verify_rows(kept, outside)

    assert verdict["valid"] is False
    assert "not a verified row" in verdict["error"]


# ── without a key: a forged removal passes, but it is on the record ─────────


async def test_unkeyed_a_forged_record_in_the_chain_passes_and_is_listed(open_store):
    """The limitation, stated: SHA-256 has no secret, so whoever can write the
    database can also write a well-formed record. What changes is that the
    removal can no longer be silent: it is a row, dated, counted, and visible
    against any head taken before it."""
    store = await open_store()
    for i in range(6):
        await _log(store, 6 - i, f"r{i}")
    rows = await _all_rows(store)
    head = await store.get_audit_head()

    await _exec(store, "DELETE FROM audit_log WHERE id IN (?, ?)", rows[2]["id"], rows[3]["id"])
    await _insert_raw(store, _forged_record(rows, 2, 3), audit_chain.CHAIN_V2, rows[-1]["entry_hash"])

    verdict = await store.verify_audit_chain({"id": head["id"], "hash": head["hash"]})

    assert verdict["valid"] is True, verdict
    assert verdict["keyed"] is False
    assert [(r["reason"], r["rows"]) for r in verdict["removals"]] == [("retention", 2)]
    assert verdict["anchor"]["rows_removed_since"] == 2


# ── with a key: the same forgery fails ──────────────────────────────────────


async def test_keyed_rows_are_sealed_with_the_key(open_store):
    store = await open_store(key=KEY_A)
    await _log(store, 1, "r0")
    row = (await _all_rows(store))[0]

    assert row["chain_v"] == audit_chain.CHAIN_V3
    assert row["entry_hash"] == audit_chain.entry_hash("GENESIS", row, 3, KEY_A.encode())
    assert row["entry_hash"] != audit_chain.entry_hash("GENESIS", row, 2)
    verdict = await store.verify_audit_chain()
    assert verdict["valid"] is True and verdict["keyed"] is True
    assert verdict["formats"] == {"v3": 1}


async def test_keyed_a_forged_unkeyed_record_is_a_break(open_store):
    store = await open_store(key=KEY_A)
    for i in range(6):
        await _log(store, 6 - i, f"r{i}")
    rows = await _all_rows(store)

    await _exec(store, "DELETE FROM audit_log WHERE id IN (?, ?)", rows[2]["id"], rows[3]["id"])
    # Without the key the only row the attacker can seal is an unkeyed one.
    await _insert_raw(store, _forged_record(rows, 2, 3), audit_chain.CHAIN_V2, rows[-1]["entry_hash"])

    verdict = await store.verify_audit_chain()

    assert verdict["valid"] is False


async def test_keyed_a_record_sealed_with_another_key_is_a_break(open_store):
    store = await open_store(key=KEY_A)
    for i in range(6):
        await _log(store, 6 - i, f"r{i}")
    rows = await _all_rows(store)

    await _exec(store, "DELETE FROM audit_log WHERE id IN (?, ?)", rows[2]["id"], rows[3]["id"])
    await _insert_raw(
        store, _forged_record(rows, 2, 3), audit_chain.CHAIN_V3, rows[-1]["entry_hash"],
        key=b"not the key, but long enough to look like one",
    )

    verdict = await store.verify_audit_chain()

    assert verdict["valid"] is False


async def test_keyed_appending_an_unkeyed_row_is_a_break(open_store):
    store = await open_store(key=KEY_A)
    await _log(store, 2, "r0")
    last = (await _all_rows(store))[-1]
    forged = audit_chain.normalise({"ts": int(time.time()), "req_id": "forged", "session_id": "s"})

    await _insert_raw(store, forged, audit_chain.CHAIN_V2, last["entry_hash"])

    verdict = await store.verify_audit_chain()
    assert verdict["valid"] is False
    assert "format goes down" in verdict["error"]


async def test_keyed_editing_a_row_and_recomputing_unkeyed_is_a_break(open_store):
    store = await open_store(key=KEY_A)
    for i in range(3):
        await _log(store, 3 - i, f"r{i}")
    victim = (await _all_rows(store))[1]
    victim["cost_usd"] = 0.0
    recomputed = audit_chain.entry_hash(victim["prev_hash"], victim, audit_chain.CHAIN_V2)

    await _exec(
        store,
        "UPDATE audit_log SET cost_usd = 0, entry_hash = ? WHERE id = ?",
        recomputed, victim["id"],
    )

    assert (await store.verify_audit_chain())["valid"] is False


async def test_a_keyed_chain_cannot_be_verified_without_its_key(open_store):
    store = await open_store(key=KEY_A)
    await _log(store, 1, "r0")

    reopened = await open_store()  # the key is gone from the environment
    verdict = await reopened.verify_audit_chain()

    assert verdict["valid"] is False
    assert audit_chain.KEY_ENV in verdict["error"]


async def test_rows_sealed_under_a_retired_key_still_verify(open_store):
    store = await open_store(key=KEY_A)
    await _log(store, 2, "old-key")

    rotated = await open_store(key=KEY_B, previous=KEY_A)
    await _log(rotated, 1, "new-key")
    verdict = await rotated.verify_audit_chain()
    assert verdict["valid"] is True, verdict
    assert verdict["verified"] == 2

    forgotten = await open_store(key=KEY_B)  # the old key was dropped too early
    assert (await forgotten.verify_audit_chain())["valid"] is False


async def test_turning_the_key_on_continues_the_unkeyed_chain(open_store):
    store = await open_store()
    await _log(store, 2, "unkeyed")

    keyed = await open_store(key=KEY_A)
    await _log(keyed, 1, "keyed")
    verdict = await keyed.verify_audit_chain()

    assert verdict["valid"] is True, verdict
    assert verdict["formats"] == {"v2": 1, "v3": 1}


async def test_turning_the_key_off_breaks_the_chain(open_store):
    store = await open_store(key=KEY_A)
    await _log(store, 2, "keyed")

    unkeyed = await open_store(previous=KEY_A)  # still able to verify the old rows
    await _log(unkeyed, 1, "unkeyed")
    verdict = await unkeyed.verify_audit_chain()

    assert verdict["valid"] is False
    assert "format goes down" in verdict["error"]


@pytest.mark.parametrize("name", [audit_chain.KEY_ENV, audit_chain.PREVIOUS_KEYS_ENV])
def test_a_short_key_is_refused_not_used(name):
    with pytest.raises(ValueError, match="at least 32"):
        audit_chain.keys_from_env({name: "too-short"})


def test_no_key_configured_means_unkeyed():
    assert audit_chain.keys_from_env({}) == (None, ())
    write, accepted = audit_chain.keys_from_env(
        {audit_chain.KEY_ENV: KEY_A, audit_chain.PREVIOUS_KEYS_ENV: f"{KEY_B}, "}
    )
    assert write == KEY_A.encode() and accepted == (KEY_A.encode(), KEY_B.encode())


# ── the encoding ────────────────────────────────────────────────────────────


def test_moving_a_separator_between_fields_changes_the_hash():
    a = audit_chain.normalise({"ts": 1, "session_id": "alice|kp", "key_prefix": "x"})
    b = audit_chain.normalise({"ts": 1, "session_id": "alice", "key_prefix": "kp|x"})

    # The format-1 preimage could not tell these two rows apart.
    assert audit_chain.entry_hash("GENESIS", a, 1) == audit_chain.entry_hash("GENESIS", b, 1)
    assert audit_chain.entry_hash("GENESIS", a, 2) != audit_chain.entry_hash("GENESIS", b, 2)
    assert audit_chain.entry_hash("GENESIS", a, 3, b"k" * 32) != audit_chain.entry_hash(
        "GENESIS", b, 3, b"k" * 32
    )


def test_a_keyed_hash_needs_its_key_and_an_unknown_format_is_refused():
    row = audit_chain.normalise({"ts": 1})
    with pytest.raises(ValueError):
        audit_chain.entry_hash("GENESIS", row, 3)
    with pytest.raises(ValueError):
        audit_chain.entry_hash("GENESIS", row, 9)


async def test_values_that_change_type_in_the_database_still_verify(open_store):
    """An integer in a REAL column comes back a float; None comes back '' or 0."""
    store = await open_store()
    await _log(store, 1, "ints", cost_usd=0, latency_ms=3)
    await _log(store, 1, "odd", cost_usd=float("nan"), latency_ms=float("inf"), blocked=True)
    await _log(store, 1, "unicode", block_reason="caffè | 漢字  ", metadata='{"a":"|"}')

    verdict = await store.verify_audit_chain()

    assert verdict["valid"] is True, verdict
    assert verdict["verified"] == 3


# ── rows written by earlier releases ────────────────────────────────────────


async def _seed_format_1(store, n):
    prev, rows = audit_chain.GENESIS, []
    for i in range(n):
        row = audit_chain.normalise(
            {"ts": int(time.time()) - (n - i) * DAY, "req_id": f"old{i}", "session_id": "s"}
        )
        digest = await _insert_raw(store, row, audit_chain.CHAIN_V1, prev)
        rows.append({**row, "prev_hash": prev, "entry_hash": digest})
        prev = digest
    return rows


async def test_a_chain_begun_by_an_earlier_release_goes_on_in_the_new_format(open_store):
    store = await open_store()
    await _seed_format_1(store, 3)
    await _log(store, 0, "new")

    verdict = await store.verify_audit_chain()

    assert verdict["valid"] is True, verdict
    assert verdict["formats"] == {"v1": 3, "v2": 1}


async def test_a_format_1_row_after_newer_rows_is_a_break(open_store):
    store = await open_store()
    await _log(store, 1, "new")
    last = (await _all_rows(store))[-1]
    old_style = audit_chain.normalise({"ts": int(time.time()), "req_id": "x", "session_id": "s"})

    await _insert_raw(store, old_style, audit_chain.CHAIN_V1, last["entry_hash"])

    verdict = await store.verify_audit_chain()
    assert verdict["valid"] is False and "format goes down" in verdict["error"]


async def test_removal_records_left_in_app_state_are_carried_into_the_chain_once(open_store):
    store = await open_store()
    old = await _seed_format_1(store, 4)
    # What 1.37.22 left behind after a retention purge of the two oldest rows.
    await _exec(store, "DELETE FROM audit_log WHERE req_id IN (?, ?)", "old0", "old1")
    await store.set_state(
        audit_chain.GAPS_KEY,
        [{"start": "GENESIS", "end": old[1]["entry_hash"], "rows": 2, "reason": "retention", "at": 1}],
    )

    upgraded = await open_store()  # the first start of this release
    verdict = await upgraded.verify_audit_chain()

    assert verdict["valid"] is True, verdict
    assert verdict["rows_removed"] == 2
    assert [(r["reason"], r["rows"]) for r in verdict["removals"]] == [("imported", 2)]

    # Once only: app_state is not read again, whatever is written there.
    rows = await _all_rows(upgraded)
    await _exec(upgraded, "DELETE FROM audit_log WHERE id = ?", rows[1]["id"])
    await upgraded.set_state(
        audit_chain.GAPS_KEY,
        [{"start": rows[1]["prev_hash"], "end": rows[1]["entry_hash"], "rows": 1,
          "reason": "retention", "at": 2}],
    )
    again = await open_store()
    assert (await again.verify_audit_chain())["valid"] is False
    assert len(await _all_rows(again)) == len(rows) - 1  # and nothing was appended for it


# ── records that outlive the rows recording them ────────────────────────────


async def test_the_chain_stays_valid_once_the_first_purge_record_is_itself_purged(
    open_store, monkeypatch
):
    store = await open_store()
    clock = [time.time()]
    monkeypatch.setattr(time, "time", lambda: clock[0])

    for i, age in enumerate([200, 150, 10]):
        await _log(store, age, f"r{i}")
    await store.purge_expired(90)  # removes two, records it now
    clock[0] += 100 * DAY  # the record, and the row that survived, are now expired
    await _log(store, 0, "fresh")
    result = await store.purge_expired(90)

    assert result["audit_deleted"] == 2  # the old survivor and the first record
    verdict = await store.verify_audit_chain()
    assert verdict["valid"] is True, verdict
    assert [req for req in [r["req_id"] for r in await _all_rows(store)]] == [
        "fresh",
        "audit-removal-retention",
    ]
    # Everything ever removed, in the one record that is left.
    assert verdict["rows_removed"] == 4
    assert [r["rows"] for r in verdict["removals"]] == [4]


async def test_an_erasure_next_to_the_purged_rows_is_bridged_through_both_records(open_store):
    store = await open_store()
    await _log(store, 200, "expired")
    await _log(store, 5, "bob-1", session="bob-session-22")
    await _log(store, 4, "alice-1", session="alice-session-1")

    await store.delete_subject_data("bob-session-22")
    await store.purge_expired(90)

    verdict = await store.verify_audit_chain()
    assert verdict["valid"] is True, verdict
    assert verdict["rows_removed"] == 2


async def test_an_erasure_with_many_runs_is_recorded_over_several_rows(open_store, monkeypatch):
    monkeypatch.setattr(audit_chain, "SEGMENTS_PER_ROW", 2)
    store = await open_store()
    for i in range(10):
        await _log(store, 10 - i, f"r{i}", session="bob-session-22" if i % 2 else "alice-session-1")

    await store.delete_subject_data("bob-session-22")  # five separate runs

    verdict = await store.verify_audit_chain()
    assert verdict["valid"] is True, verdict
    records = [r for r in await _all_rows(store) if r["session_id"] == audit_chain.SYSTEM_SESSION]
    assert len(records) == 3
    assert sum(len(json.loads(r["metadata"])["segments"]) for r in records) == 5
    assert sum(r["rows"] for r in verdict["removals"]) == 5


async def test_a_removal_after_a_recorded_head_is_counted_against_it(open_store):
    store = await open_store()
    for i, age in enumerate([200, 190, 10, 5]):
        await _log(store, age, f"r{i}")
    head = await store.get_audit_head()

    await store.purge_expired(90)

    verdict = await store.verify_audit_chain({"id": head["id"], "hash": head["hash"]})
    assert verdict["valid"] is True, verdict
    assert verdict["anchor"] == {"id": head["id"], "status": "ok", "rows_removed_since": 2}


# ── the chain's own rows are nobody's data ──────────────────────────────────


@pytest.mark.parametrize("subject", sorted(audit_chain.RESERVED_SUBJECTS))
async def test_the_chains_own_rows_cannot_be_erased_as_a_subject(open_store, subject):
    store = await open_store()
    await _log(store, 200, "expired")
    await _log(store, 1, "kept")
    await store.purge_expired(90)
    before = await _all_rows(store)

    with pytest.raises(ValueError, match="reserved"):
        await store.delete_subject_data(subject)

    assert await _all_rows(store) == before

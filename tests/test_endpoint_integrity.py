"""Endpoint rows hold one copy of each fact, and the database refuses nonsense.

* latency_ms / success_rate lived in their columns, inside the metadata JSON and
  in the in-memory routing average, and update_status copied from one to another.
  The columns are the stored truth now: metadata is stored without them.
* The schema had no CHECK constraints, so success_rate=5 or status=99 could be
  written by anything that bypassed the pydantic model (an admin's SQL, a
  restore, a future store method). The endpoints table now enforces its ranges,
  and an existing database is migrated, with out-of-range values brought into
  range first.
"""

import json
import os
import sqlite3

import pytest

from models import EndpointStatus, LLMEndpoint, split_endpoint_stats
from store import schema
from store.sql_store import SQLiteStore

TEST_POSTGRES_DSN = os.environ.get("TEST_POSTGRES_DSN", "")

OLD_SQLITE_ENDPOINTS = """
CREATE TABLE endpoints (
    id TEXT PRIMARY KEY, url TEXT UNIQUE, status INTEGER, metadata TEXT,
    last_verified TEXT, latency_ms REAL, success_rate REAL
)
"""
OLD_PG_ENDPOINTS = """
CREATE TABLE endpoints (
    id VARCHAR(255) PRIMARY KEY, url VARCHAR(512) UNIQUE, status INTEGER, metadata TEXT,
    last_verified VARCHAR(50), latency_ms DOUBLE PRECISION, success_rate DOUBLE PRECISION
)
"""


def _endpoint(id_="ep1", **kwargs):
    return LLMEndpoint(
        id=id_, url=f"http://{id_}.test/v1", status=EndpointStatus.VERIFIED, **kwargs
    )


# ── one copy of health ──────────────────────────────────────────────────────


def test_split_endpoint_stats_separates_health_from_the_rest():
    clean, stats = split_endpoint_stats(
        {"provider": "x", "latency_ms": 5, "success_rate": 0.5, "priority": 1}
    )
    assert clean == {"provider": "x", "priority": 1}
    assert stats == {"latency_ms": 5, "success_rate": 0.5}
    assert split_endpoint_stats(None) == ({}, {})


def test_the_model_moves_stats_out_of_metadata():
    ep = _endpoint(metadata={"provider": "x", "latency_ms": 120.0, "success_rate": 0.9})

    assert "latency_ms" not in ep.metadata and "success_rate" not in ep.metadata
    assert ep.latency_ms == 120.0 and ep.success_rate == 0.9


def test_a_field_with_a_value_wins_over_a_stale_metadata_copy():
    ep = _endpoint(latency_ms=10.0, metadata={"latency_ms": 999.0})
    assert ep.latency_ms == 10.0 and "latency_ms" not in ep.metadata


def test_the_metadata_copy_fills_a_missing_column():
    ep = _endpoint(latency_ms=None, metadata={"latency_ms": 7.0})
    assert ep.latency_ms == 7.0


@pytest.fixture
async def sqlite_store(tmp_path):
    s = SQLiteStore(str(tmp_path / "ep.db"))
    await s.init_db()
    yield s
    await s.close()


async def _raw_metadata(store, id_):
    conn = await store._get_conn()
    async with conn.execute("SELECT metadata, latency_ms, success_rate FROM endpoints WHERE id = ?", (id_,)) as cur:
        return await cur.fetchone()


async def test_add_endpoint_does_not_store_a_second_copy_in_metadata(sqlite_store):
    await sqlite_store.add_endpoint(
        _endpoint(latency_ms=12.0, success_rate=0.8, metadata={"provider": "x", "priority": 3})
    )

    metadata, latency, rate = await _raw_metadata(sqlite_store, "ep1")

    assert json.loads(metadata) == {"provider": "x", "priority": 3}
    assert (latency, rate) == (12.0, 0.8)


async def test_update_status_writes_stats_to_columns_only(sqlite_store):
    await sqlite_store.add_endpoint(_endpoint(metadata={"provider": "x"}))

    await sqlite_store.update_status(
        "ep1", EndpointStatus.VERIFIED,
        {"provider": "x", "priority": 2, "latency_ms": 30.0, "success_rate": 0.7},
    )

    metadata, latency, rate = await _raw_metadata(sqlite_store, "ep1")
    assert json.loads(metadata) == {"provider": "x", "priority": 2}
    assert (latency, rate) == (30.0, 0.7)


async def test_a_stats_only_update_leaves_the_stored_metadata_alone(sqlite_store):
    await sqlite_store.add_endpoint(_endpoint(metadata={"provider": "anthropic", "models": ["c"]}))

    await sqlite_store.update_status("ep1", EndpointStatus.VERIFIED, {"latency_ms": 55.0})

    metadata, latency, _ = await _raw_metadata(sqlite_store, "ep1")
    assert json.loads(metadata) == {"provider": "anthropic", "models": ["c"]}
    assert latency == 55.0
    (loaded,) = await sqlite_store.get_pool()
    assert loaded.provider == "anthropic" and loaded.latency_ms == 55.0


async def test_a_legacy_row_with_stale_copies_reads_with_the_columns_authoritative(sqlite_store):
    conn = await sqlite_store._get_conn()
    await conn.execute(
        "INSERT INTO endpoints (id, url, status, metadata, latency_ms, success_rate) VALUES (?,?,?,?,?,?)",
        ("old", "http://old.test/v1", 3, json.dumps({"provider": "x", "latency_ms": 999, "success_rate": 0.1}), 20.0, 0.95),
    )
    await conn.commit()

    (ep,) = await sqlite_store.get_all()

    assert ep.latency_ms == 20.0 and ep.success_rate == 0.95
    assert ep.metadata == {"provider": "x"}


# ── the database refuses nonsense ───────────────────────────────────────────


@pytest.fixture(params=["sqlite", "postgres"])
async def store(request, tmp_path):
    if request.param == "sqlite":
        s = SQLiteStore(str(tmp_path / "chk.db"))
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


async def _insert(store, id_, status=3, latency=1.0, rate=0.5):
    if hasattr(store, "_get_conn"):
        conn = await store._get_conn()
        await conn.execute(
            "INSERT INTO endpoints (id, url, status, metadata, latency_ms, success_rate) VALUES (?,?,?,?,?,?)",
            (id_, f"http://{id_}.test", status, "{}", latency, rate),
        )
        await conn.commit()
    else:
        pool = await store.init_pool()
        await pool.execute(
            "INSERT INTO endpoints (id, url, status, metadata, latency_ms, success_rate) VALUES ($1,$2,$3,$4,$5,$6)",
            id_, f"http://{id_}.test", status, "{}", latency, rate,
        )


def _violation():
    errors = [sqlite3.IntegrityError]
    try:
        import asyncpg

        errors.append(asyncpg.exceptions.CheckViolationError)
    except ImportError:  # pragma: no cover
        pass
    return tuple(errors)


@pytest.mark.parametrize(
    "kwargs",
    [{"status": 99}, {"status": -1}, {"rate": 5.0}, {"rate": -0.1}, {"latency": -1.0}],
)
async def test_an_out_of_range_value_cannot_be_written(store, kwargs):
    with pytest.raises(_violation()):
        await _insert(store, "bad", **kwargs)


@pytest.mark.parametrize(
    "kwargs",
    [{"status": 0}, {"status": 3}, {"rate": 0.0}, {"rate": 1.0}, {"latency": 0.0}, {"rate": None}, {"latency": None}],
)
async def test_the_boundaries_and_unmeasured_values_are_accepted(store, kwargs):
    await _insert(store, "ok", **kwargs)


async def test_the_status_range_follows_the_enum():
    assert f"BETWEEN {min(EndpointStatus).value} AND {max(EndpointStatus).value}" in (
        schema.create_table_sql("endpoints", schema.SQLITE)
    )


# ── migrating an existing database ──────────────────────────────────────────


async def test_an_existing_sqlite_database_gets_the_constraints_and_keeps_its_rows(tmp_path):
    path = str(tmp_path / "old.db")
    db = sqlite3.connect(path)
    db.execute(OLD_SQLITE_ENDPOINTS)
    db.execute("CREATE INDEX idx_endpoints_status ON endpoints(status)")
    rows = [
        ("good", "http://good.test", 3, '{"provider":"anthropic"}', 12.0, 0.9),
        ("badstatus", "http://bs.test", 99, '{"provider":"x"}', 1.0, 0.5),
        ("badrate", "http://br.test", 3, "{}", 1.0, 7.5),
        ("neglat", "http://nl.test", 3, "{}", -4.0, 0.5),
        ("unmeasured", "http://um.test", 2, "{}", None, None),
    ]
    db.executemany(
        "INSERT INTO endpoints (id, url, status, metadata, latency_ms, success_rate) VALUES (?,?,?,?,?,?)",
        rows,
    )
    db.commit()
    db.close()

    store = SQLiteStore(path)
    await store.init_db()
    try:
        conn = await store._get_conn()
        async with conn.execute(
            "SELECT id, status, metadata, latency_ms, success_rate FROM endpoints ORDER BY id"
        ) as cur:
            migrated = {r[0]: tuple(r[1:]) for r in await cur.fetchall()}
        async with conn.execute("SELECT sql FROM sqlite_master WHERE name='endpoints'") as cur:
            ddl = (await cur.fetchone())[0]
        async with conn.execute("SELECT name FROM sqlite_master WHERE type='index' AND name='idx_endpoints_status'") as cur:
            index = await cur.fetchone()
        async with conn.execute("SELECT name FROM _migrations WHERE name='002_endpoints_range_checks'") as cur:
            recorded = await cur.fetchone()

        assert set(migrated) == {r[0] for r in rows}, "no row may be lost"
        assert migrated["good"] == (3, '{"provider":"anthropic"}', 12.0, 0.9)
        assert migrated["badstatus"][0] == EndpointStatus.IGNORED.value
        assert migrated["badrate"][3] == 1.0
        assert migrated["neglat"][2] == 0
        assert migrated["unmeasured"] == (2, "{}", None, None)
        assert "CHECK" in ddl and index and recorded

        with pytest.raises(sqlite3.IntegrityError):
            await _insert(store, "now-refused", rate=2.0)
    finally:
        await store.close()


async def test_running_the_migration_again_changes_nothing(tmp_path):
    path = str(tmp_path / "again.db")
    store = SQLiteStore(path)
    await store.init_db()
    await store.add_endpoint(_endpoint(latency_ms=3.0, success_rate=0.3, metadata={"provider": "x"}))
    await store.close()

    again = SQLiteStore(path)
    await again.init_db()
    try:
        (ep,) = await again.get_all()
        assert ep.latency_ms == 3.0 and ep.provider == "x"
    finally:
        await again.close()


async def test_an_existing_postgres_database_gets_the_constraints():
    if not TEST_POSTGRES_DSN:
        pytest.skip("TEST_POSTGRES_DSN not set")
    asyncpg = pytest.importorskip("asyncpg")
    from store.pg_store import PostgresStore

    raw = await asyncpg.connect(TEST_POSTGRES_DSN)
    try:
        await raw.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        await raw.execute(OLD_PG_ENDPOINTS)
        await raw.execute(
            "INSERT INTO endpoints (id, url, status, metadata, latency_ms, success_rate) VALUES "
            "('good','http://g.test',3,'{}',5,0.5),('bs','http://b.test',99,'{}',1,0.5),"
            "('br','http://r.test',3,'{}',1,7.5),('nl','http://n.test',3,'{}',-4,0.5)"
        )
    finally:
        await raw.close()

    store = PostgresStore(TEST_POSTGRES_DSN)
    await store.init_db()
    try:
        pool = await store.init_pool()
        rows = {r["id"]: r for r in await pool.fetch("SELECT * FROM endpoints")}
        assert set(rows) == {"good", "bs", "br", "nl"}
        assert rows["bs"]["status"] == EndpointStatus.IGNORED.value
        assert rows["br"]["success_rate"] == 1.0 and rows["nl"]["latency_ms"] == 0
        names = {r["conname"] for r in await pool.fetch("SELECT conname FROM pg_constraint WHERE conrelid = 'endpoints'::regclass")}
        assert {"endpoints_status_check", "endpoints_latency_ms_check", "endpoints_success_rate_check"} <= names
        with pytest.raises(asyncpg.exceptions.CheckViolationError):
            await _insert(store, "now-refused", status=42)
        await store.close()
        # Running init_db again must not trip over the existing constraints.
        again = PostgresStore(TEST_POSTGRES_DSN)
        await again.init_db()
        await again.close()
    finally:
        await store.close()

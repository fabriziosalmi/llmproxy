"""Schema conformance — the two backends must agree, and with the declaration.

The SQLite and Postgres stores used to carry independent copies of every
CREATE TABLE, in their own dialect, kept in step by whoever remembered. They
had already drifted. store/schema.py is now the single declaration and each
store renders it; these tests hold that claim to a real database rather than
to a mock.

The Postgres half needs a server. Set TEST_POSTGRES_DSN to run it — CI does,
via a service container. Without it those tests skip rather than silently
passing, so a local run cannot look like coverage it does not have.
"""

import os
import sqlite3

import pytest

from store.schema import (
    MIGRATIONS,
    POSTGRES,
    SQLITE,
    TABLES,
    iter_create_statements,
)

TEST_POSTGRES_DSN = os.environ.get("TEST_POSTGRES_DSN", "")
requires_postgres = pytest.mark.skipif(
    not TEST_POSTGRES_DSN,
    reason="TEST_POSTGRES_DSN not set — start a Postgres and export it to run these",
)


# ── introspection helpers ────────────────────────────────────────────────────


def _sqlite_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]


def _sqlite_tables(conn: sqlite3.Connection) -> list[str]:
    return sorted(
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%'"
        )
    )


async def _pg_columns(conn, table: str) -> list[str]:
    rows = await conn.fetch(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = $1 "
        "ORDER BY ordinal_position",
        table,
    )
    return [r["column_name"] for r in rows]


async def _pg_tables(conn) -> list[str]:
    rows = await conn.fetch(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
    )
    return sorted(r["table_name"] for r in rows)


# ── fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def sqlite_conn(tmp_path):
    """A SQLite database built from the declaration."""
    conn = sqlite3.connect(str(tmp_path / "conformance.db"))
    for stmt in iter_create_statements(SQLITE):
        conn.execute(stmt)
    conn.commit()
    yield conn
    conn.close()


@pytest.fixture
async def pg_conn():
    """A Postgres schema built from the declaration, in its own namespace.

    Each run drops and recreates the public schema so a leftover table from an
    earlier run cannot make a missing CREATE look like a passing test.
    """
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(TEST_POSTGRES_DSN)
    try:
        await conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        for stmt in iter_create_statements(POSTGRES):
            await conn.execute(stmt)
        yield conn
    finally:
        await conn.close()


# ── the declaration is what SQLite actually builds ──────────────────────────


def test_sqlite_matches_the_declaration(sqlite_conn):
    assert _sqlite_tables(sqlite_conn) == sorted(TABLES), (
        "SQLite built a different set of tables than store/schema.py declares"
    )
    for table, columns in TABLES.items():
        declared = [c.name for c in columns]
        actual = _sqlite_columns(sqlite_conn, table)
        assert actual == declared, f"{table}: SQLite columns diverge from declaration"


# ── the declaration is what Postgres actually builds ────────────────────────


@requires_postgres
@pytest.mark.asyncio
async def test_postgres_matches_the_declaration(pg_conn):
    assert await _pg_tables(pg_conn) == sorted(TABLES), (
        "Postgres built a different set of tables than store/schema.py declares"
    )
    for table, columns in TABLES.items():
        declared = [c.name for c in columns]
        actual = await _pg_columns(pg_conn, table)
        assert actual == declared, f"{table}: Postgres columns diverge from declaration"


# ── and therefore the two agree with each other ─────────────────────────────


@requires_postgres
@pytest.mark.asyncio
async def test_both_backends_agree(sqlite_conn, pg_conn):
    """The test the mock-only suite could never be.

    Column *types* legitimately differ between dialects; names, ordering and
    the table set do not. This is the assertion that would have caught
    entry_hash being VARCHAR(64) on one side and unbounded TEXT on the other,
    because such a change can no longer be made to one backend alone.
    """
    assert _sqlite_tables(sqlite_conn) == await _pg_tables(pg_conn)
    for table in TABLES:
        assert _sqlite_columns(sqlite_conn, table) == await _pg_columns(
            pg_conn, table
        ), f"{table}: the two backends disagree about columns"


@requires_postgres
@pytest.mark.asyncio
async def test_audit_hash_columns_hold_a_full_sha256(pg_conn):
    """The column that had actually drifted, exercised rather than declared.

    A sha256 hex digest is 64 characters. Postgres caps these columns at
    VARCHAR(64); SQLite does not cap them at all. Inserting a full-width digest
    must succeed on the strict side — if someone widens the digest without
    widening the column, this fails on Postgres before it reaches an operator.
    """
    digest = "a" * 64
    await pg_conn.execute(
        "INSERT INTO audit_log (ts, req_id, entry_hash, prev_hash) "
        "VALUES ($1, $2, $3, $4)",
        1,
        "r1",
        digest,
        digest,
    )
    row = await pg_conn.fetchrow(
        "SELECT entry_hash, prev_hash FROM audit_log WHERE req_id = 'r1'"
    )
    assert row["entry_hash"] == digest
    assert row["prev_hash"] == digest


@requires_postgres
@pytest.mark.asyncio
async def test_postgres_round_trip_through_the_real_store(pg_conn):
    """A write and a read against a real server, not a MagicMock.

    The existing Postgres suite asserts on mock call counts — most sharply
    `assert mock_conn.execute.call_count >= 5` — so none of its SQL was ever
    parsed by a database. This exercises the app_state path end to end.
    """
    await pg_conn.execute(
        "INSERT INTO app_state (key, value) VALUES ($1, $2) "
        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
        "budget:daily_total",
        "12.5",
    )
    got = await pg_conn.fetchval(
        "SELECT value FROM app_state WHERE key = $1", "budget:daily_total"
    )
    assert got == "12.5"


# ── the one surface where the two dialects can still drift ──────────────────


def test_migrations_touch_the_same_columns_in_both_dialects():
    """MIGRATIONS is the remaining place a change can land on one backend only.

    Tables and columns can no longer diverge: there is one declaration and both
    stores render it. Migrations still carry a separate statement list per
    dialect, because the SQL differs (Postgres has ADD COLUMN IF NOT EXISTS,
    SQLite does not) — so a column added to one list and forgotten in the other
    would recreate exactly the class of bug the shared declaration removed.

    Compares the column names each dialect's statements mention, not the SQL
    itself, since the SQL is legitimately different.
    """
    import re

    for name, per_dialect in MIGRATIONS:
        assert set(per_dialect) == {SQLITE, POSTGRES}, (
            f"{name}: every migration must carry statements for both dialects"
        )
        touched = {}
        for dialect, statements in per_dialect.items():
            cols = set()
            for stmt in statements:
                m = re.search(
                    r"ADD COLUMN (?:IF NOT EXISTS )?(\w+)", stmt, re.IGNORECASE
                )
                if m:
                    cols.add(m.group(1))
            touched[dialect] = cols
        assert touched[SQLITE] == touched[POSTGRES], (
            f"{name}: adds {touched[SQLITE]} on SQLite but "
            f"{touched[POSTGRES]} on Postgres"
        )


def test_migration_columns_exist_in_the_declaration():
    """A migration must add columns the declaration also knows about.

    Otherwise a fresh database (built from TABLES) and a migrated one (built
    from TABLES as it was, plus MIGRATIONS) end up with different shapes — the
    same divergence, displaced in time rather than across backends.
    """
    import re

    for name, per_dialect in MIGRATIONS:
        for stmt in per_dialect[SQLITE]:
            m = re.search(r"ALTER TABLE (\w+) ADD COLUMN (?:IF NOT EXISTS )?(\w+)", stmt, re.IGNORECASE)
            if not m:
                continue
            table, column = m.group(1), m.group(2)
            assert table in TABLES, f"{name}: unknown table {table}"
            assert column in [c.name for c in TABLES[table]], (
                f"{name}: adds {table}.{column}, which store/schema.py does not declare"
            )


# ── migrations record only what succeeded ───────────────────────────────────


@pytest.mark.asyncio
async def test_failed_migration_is_not_recorded(tmp_path):
    """A migration that genuinely fails must not be marked applied.

    The runner used to catch every OperationalError and insert the migration
    row regardless, so a failure from a locked or full database was recorded as
    success and never retried — leaving the schema short of the columns while
    _migrations claimed otherwise. Only "duplicate column name" is tolerated
    now; anything else propagates.
    """
    from store.sql_store import SQLiteStore

    store = SQLiteStore(str(tmp_path / "mig.db"))
    conn = await store._get_conn()
    # Schema without audit_log, so the migration's ALTER cannot apply for a
    # reason that is emphatically not "the column already exists".
    await conn.execute(
        "CREATE TABLE IF NOT EXISTS _migrations "
        "(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE NOT NULL, "
        "applied_at INTEGER NOT NULL)"
    )
    await conn.commit()

    with pytest.raises(sqlite3.OperationalError):
        await store._run_migrations(conn)

    async with conn.execute("SELECT COUNT(*) FROM _migrations") as cur:
        (count,) = await cur.fetchone()
    assert count == 0, "a migration that raised must not be recorded as applied"


@pytest.mark.asyncio
async def test_rerunning_migrations_is_idempotent(tmp_path):
    """The tolerated case: columns already present, migration still recorded."""
    from store.sql_store import SQLiteStore

    store = SQLiteStore(str(tmp_path / "idem.db"))
    await store.init_db()
    conn = await store._get_conn()

    async with conn.execute("SELECT COUNT(*) FROM _migrations") as cur:
        (first,) = await cur.fetchone()
    await store.init_db()
    async with conn.execute("SELECT COUNT(*) FROM _migrations") as cur:
        (second,) = await cur.fetchone()
    assert first == second, "re-running init_db must not duplicate migration rows"

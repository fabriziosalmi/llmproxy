"""LLMProxy — the schema, declared once.

Both stores used to carry their own CREATE TABLE statements, in their own
dialect, kept in step by whoever remembered. They had already drifted: the
audit-chain columns were VARCHAR(64) on Postgres and unbounded TEXT on
SQLite, so the same field had two capacities depending on which backend an
operator happened to deploy.

The tables, columns, ordering and indexes now live here and each store
renders them for its dialect. A column added to one backend is a column
added to both, because there is only one place to add it.

What deliberately still differs is the *type*, because the dialects differ:
SQLite has no length constraints and stores every integer as 64-bit, while
Postgres wants explicit widths and SERIAL for autoincrement. Each column
therefore declares both spellings side by side, where a reviewer can see
them together. tests/test_schema_conformance.py asserts that what must
match does match, against both engines for real.
"""

from __future__ import annotations

from typing import Iterable, NamedTuple

SQLITE = "sqlite"
POSTGRES = "postgres"

# sha256, hex-encoded. Named so the audit-chain columns cannot drift apart
# again by someone changing one of the two literals.
_HASH_LEN = 64


class Column(NamedTuple):
    """One column, spelled for both dialects.

    `extra` carries whatever is identical across dialects — constraints and
    defaults — so it cannot be applied to one backend and forgotten on the
    other.
    """

    name: str
    sqlite: str
    postgres: str
    extra: str = ""

    def render(self, dialect: str) -> str:
        type_ = self.sqlite if dialect == SQLITE else self.postgres
        return f"{self.name} {type_}{(' ' + self.extra) if self.extra else ''}"


def _pk_autoinc() -> Column:
    """Surrogate integer primary key."""
    return Column("id", "INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")


def _text(name: str, pg_len: int, extra: str = "") -> Column:
    """Bounded text. SQLite has no VARCHAR length, Postgres does."""
    return Column(name, "TEXT", f"VARCHAR({pg_len})", extra)


def _blob_text(name: str, extra: str = "") -> Column:
    """Unbounded text on both sides — JSON blobs, free-form reasons."""
    return Column(name, "TEXT", "TEXT", extra)


def _int(name: str, extra: str = "") -> Column:
    return Column(name, "INTEGER", "INTEGER", extra)


def _bigint(name: str, extra: str = "") -> Column:
    """Epoch timestamps. SQLite INTEGER is already 64-bit."""
    return Column(name, "INTEGER", "BIGINT", extra)


def _real(name: str, extra: str = "") -> Column:
    return Column(name, "REAL", "DOUBLE PRECISION", extra)


TABLES: dict[str, list[Column]] = {
    "endpoints": [
        _text("id", 255, "PRIMARY KEY"),
        _text("url", 512, "UNIQUE"),
        _int("status"),
        _blob_text("metadata"),
        _text("last_verified", 50),
        _real("latency_ms"),
        _real("success_rate"),
    ],
    "app_state": [
        _text("key", 255, "PRIMARY KEY"),
        _blob_text("value", "NOT NULL"),
    ],
    # Spend analytics log (R2.3)
    "spend_log": [
        _pk_autoinc(),
        _bigint("ts", "NOT NULL"),
        _text("date", 50, "NOT NULL"),
        _text("key_prefix", 50, "NOT NULL DEFAULT ''"),
        _text("model", 255, "NOT NULL DEFAULT ''"),
        _text("provider", 100, "NOT NULL DEFAULT ''"),
        _int("prompt_tokens", "DEFAULT 0"),
        _int("completion_tokens", "DEFAULT 0"),
        _real("cost_usd", "DEFAULT 0.0"),
        _real("latency_ms", "DEFAULT 0.0"),
        _int("status", "DEFAULT 200"),
    ],
    # Persistent audit log (R2.10). entry_hash/prev_hash form the tamper-evident
    # chain — see SQLiteStore._audit_lock for why appends must be serialised.
    "audit_log": [
        _pk_autoinc(),
        _bigint("ts", "NOT NULL"),
        _text("req_id", 100, "NOT NULL DEFAULT ''"),
        _text("session_id", 100, "DEFAULT ''"),
        _text("key_prefix", 50, "DEFAULT ''"),
        _text("model", 255, "DEFAULT ''"),
        _text("provider", 100, "DEFAULT ''"),
        _int("status", "DEFAULT 200"),
        _int("prompt_tokens", "DEFAULT 0"),
        _int("completion_tokens", "DEFAULT 0"),
        _real("cost_usd", "DEFAULT 0.0"),
        _real("latency_ms", "DEFAULT 0.0"),
        _int("blocked", "DEFAULT 0"),
        _blob_text("block_reason", "DEFAULT ''"),
        _blob_text("metadata", "DEFAULT '{}'"),
        _text("entry_hash", _HASH_LEN, "DEFAULT ''"),
        _text("prev_hash", _HASH_LEN, "DEFAULT ''"),
    ],
    # RBAC / GDPR — referenced by delete_subject_data / export_subject_data
    "user_roles": [
        _pk_autoinc(),
        _text("subject", 255, "NOT NULL"),
        _text("email", 255, "NOT NULL DEFAULT ''"),
        _text("role", 100, "NOT NULL DEFAULT ''"),
        _bigint("granted_at", "DEFAULT 0"),
    ],
    # Schema migration tracking
    "_migrations": [
        _pk_autoinc(),
        _text("name", 255, "UNIQUE NOT NULL"),
        _bigint("applied_at", "NOT NULL"),
    ],
}

# (index name, table, column expression)
INDEXES: list[tuple[str, str, str]] = [
    ("idx_spend_date", "spend_log", "date"),
    ("idx_spend_model", "spend_log", "model, date"),
    ("idx_spend_key", "spend_log", "key_prefix, date"),
    ("idx_audit_ts", "audit_log", "ts"),
    ("idx_audit_model", "audit_log", "model"),
    ("idx_audit_key", "audit_log", "key_prefix, ts"),
    ("idx_audit_session", "audit_log", "session_id, ts"),
    ("idx_endpoints_status", "endpoints", "status"),
    ("idx_user_roles_subject", "user_roles", "subject"),
]

# Ordered, append-only. Each entry ships the statements for both dialects so a
# migration cannot land on one backend and not the other. Postgres supports
# ADD COLUMN IF NOT EXISTS; SQLite does not, which is why the runner there has
# to tolerate an already-existing column — narrowly, not by swallowing every
# OperationalError.
MIGRATIONS: list[tuple[str, dict[str, list[str]]]] = [
    (
        "001_audit_hash_columns",
        {
            SQLITE: [
                "ALTER TABLE audit_log ADD COLUMN entry_hash TEXT DEFAULT ''",
                "ALTER TABLE audit_log ADD COLUMN prev_hash TEXT DEFAULT ''",
            ],
            POSTGRES: [
                f"ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS entry_hash VARCHAR({_HASH_LEN}) DEFAULT ''",
                f"ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS prev_hash VARCHAR({_HASH_LEN}) DEFAULT ''",
            ],
        },
    ),
]


def create_table_sql(table: str, dialect: str) -> str:
    """Render CREATE TABLE IF NOT EXISTS for one table in one dialect."""
    cols = ",\n    ".join(c.render(dialect) for c in TABLES[table])
    return f"CREATE TABLE IF NOT EXISTS {table} (\n    {cols}\n)"


def create_index_sql(name: str, table: str, columns: str) -> str:
    return f"CREATE INDEX IF NOT EXISTS {name} ON {table}({columns})"


def iter_create_statements(dialect: str) -> Iterable[str]:
    """Every DDL statement needed to build the schema, in dependency order."""
    for table in TABLES:
        yield create_table_sql(table, dialect)
    for name, table, columns in INDEXES:
        yield create_index_sql(name, table, columns)


def column_names(table: str) -> list[str]:
    return [c.name for c in TABLES[table]]

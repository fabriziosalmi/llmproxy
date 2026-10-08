import asyncio
import contextlib
import json
import logging
import os
import sqlite3
from typing import Any

import aiosqlite

from models import EndpointStatus, LLMEndpoint, split_endpoint_stats

from . import audit_chain
from .pool_cache import PoolCache
from .rows import endpoints_from_rows
from .schema import MIGRATIONS, SQLITE, iter_create_statements

logger = logging.getLogger(__name__)


class SQLiteStore:
    """Robust Asynchronous SQLite-based storage for LLM endpoints and metadata.

    Uses a single persistent connection (like CacheBackend) instead of
    opening a new connection per query. That connection is also one
    transaction: a statement that runs while another writer is between its
    statement and its commit joins that writer's transaction, and the first
    ``commit()`` ends it for both. So every write goes through ``_write()``,
    which holds ``_write_lock`` for the whole statement-to-commit span. It also
    gives the audit log's hash chain its linearity (the last hash is read and
    the next row inserted with nobody else writing in between).
    """

    def __init__(self, db_path: str = "data/endpoints.db"):
        self.db_path = db_path
        self._conn: aiosqlite.Connection | None = None
        self._conn_lock = asyncio.Lock()
        # Protects conn.row_factory mutations — row_factory is connection-level
        # in aiosqlite, so concurrent queries that toggle it would corrupt each
        # other's result types.
        self._row_factory_lock = asyncio.Lock()
        # One writer at a time on the shared connection (see _write). This also
        # keeps the audit chain linear: without it two simultaneous requests
        # read the same prev_hash, compute diverging entry_hashes, and the chain
        # splits, so verify_audit_chain() reports permanent tamper detection.
        self._write_lock = asyncio.Lock()
        self._pool_cache = PoolCache()

    async def _get_conn(self) -> aiosqlite.Connection:
        """Return the persistent connection, creating it if needed.

        Uses double-check locking to avoid creating duplicate connections
        when called concurrently (e.g. during startup burst).
        """
        if self._conn is not None:
            return self._conn
        async with self._conn_lock:
            if self._conn is None:
                parent = os.path.dirname(self.db_path)
                if parent:
                    os.makedirs(parent, exist_ok=True)
                self._conn = await aiosqlite.connect(self.db_path)
                await self._conn.execute("PRAGMA journal_mode=WAL")
                await self._conn.execute("PRAGMA synchronous=NORMAL")
                await self._conn.execute("PRAGMA busy_timeout=5000")
            return self._conn

    @contextlib.asynccontextmanager
    async def _write(self):
        """Exclusive use of the connection for one write transaction.

        Commits when the block finishes and rolls back when it raises, so a
        method cannot leave a transaction open by returning early. A writer
        cancelled between its statement and its commit (a client disconnecting
        mid-request) never reaches either; its half-done transaction would make
        the next ``BEGIN`` fail with "cannot start a transaction within a
        transaction" on every later request, so whatever is pending when the
        lock is taken is rolled back first. Nobody else holds the lock, so it
        cannot be anyone's live work.
        """
        async with self._write_lock:
            conn = await self._get_conn()
            if conn.in_transaction:
                await conn.rollback()
            try:
                yield conn
                await conn.commit()
            except BaseException:
                with contextlib.suppress(Exception):
                    await conn.rollback()
                raise

    async def init_db(self):
        """Build the schema from the single declaration in store/schema.py.

        The CREATE statements used to live here in full, duplicated in
        store/pg_store.py in Postgres dialect and kept in step by hand. They
        had already drifted. Rendering them from one declaration means a
        column added for one backend is added for both.
        """
        async with self._write() as conn:
            for stmt in iter_create_statements(SQLITE):
                await conn.execute(stmt)
            await self._run_migrations(conn)

    async def _run_migrations(self, conn) -> None:
        """Apply pending migrations, recording only the ones that succeeded.

        This used to wrap each statement in `except sqlite3.OperationalError:
        pass` and then record the migration as applied regardless. That handler
        was written for one expected cause — the column already exists from the
        pre-migration era — but OperationalError also covers "database is
        locked", "disk I/O error" and "database or disk is full". A migration
        that genuinely failed was marked done and never retried, leaving the
        database permanently missing the columns while _migrations asserted
        otherwise.

        Now only the already-exists case is tolerated, everything else
        propagates, and the row is written only after every statement in the
        migration has succeeded.
        """
        import time as _time

        for mig_name, per_dialect in MIGRATIONS:
            async with conn.execute(
                "SELECT 1 FROM _migrations WHERE name = ?", (mig_name,)
            ) as cur:
                if await cur.fetchone():
                    continue
            for stmt in per_dialect[SQLITE]:
                try:
                    await conn.execute(stmt)
                except sqlite3.OperationalError as e:
                    # SQLite has no ADD COLUMN IF NOT EXISTS, so re-running a
                    # migration against a database that predates the tracking
                    # table is expected. Anything else is a real failure.
                    if "duplicate column name" not in str(e).lower():
                        raise
            await conn.execute(
                "INSERT INTO _migrations (name, applied_at) VALUES (?, ?)",
                (mig_name, int(_time.time())),
            )

    async def add_endpoint(self, endpoint: LLMEndpoint):
        async with self._write() as conn:
            await conn.execute(
                # ON CONFLICT (id), as Postgres does, not INSERT OR REPLACE: REPLACE
                # also deletes whichever *other* row holds the same url (the
                # column is UNIQUE), so adding a new id for an existing URL
                # silently destroyed the other endpoint on SQLite while Postgres
                # raised. Now both refuse, and only the same id is updated.
                "INSERT INTO endpoints (id, url, status, metadata, latency_ms, success_rate) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET url = excluded.url, status = excluded.status, "
                "metadata = excluded.metadata, latency_ms = excluded.latency_ms, "
                "success_rate = excluded.success_rate",
                (
                    endpoint.id,
                    str(endpoint.url),
                    endpoint.status.value,
                    json.dumps(split_endpoint_stats(endpoint.metadata)[0]),
                    endpoint.latency_ms,
                    endpoint.success_rate,
                ),
            )
        self._pool_cache.invalidate()

    async def update_status(
        self, endpoint_id: str, status: EndpointStatus, metadata: dict | None = None
    ):
        """Set an endpoint's status, and optionally its metadata and health.

        ``latency_ms`` / ``success_rate`` in ``metadata`` are written to their
        columns and are not stored in the metadata JSON as well. A call that
        carries only those stats updates the columns and leaves the stored
        metadata (provider, models, priority) as it was.
        """
        clean, stats = split_endpoint_stats(metadata)
        latency_ms = stats.get("latency_ms")
        success_rate = stats.get("success_rate")

        async with self._write() as conn:
            if clean:
                await conn.execute(
                    "UPDATE endpoints SET status = ?, metadata = ?, latency_ms = COALESCE(?, latency_ms), success_rate = COALESCE(?, success_rate), last_verified = CURRENT_TIMESTAMP WHERE id = ?",
                    (
                        status.value,
                        json.dumps(clean),
                        latency_ms,
                        success_rate,
                        endpoint_id,
                    ),
                )
            else:
                await conn.execute(
                    "UPDATE endpoints SET status = ?, latency_ms = COALESCE(?, latency_ms), success_rate = COALESCE(?, success_rate), last_verified = CURRENT_TIMESTAMP WHERE id = ?",
                    (status.value, latency_ms, success_rate, endpoint_id),
                )
        self._pool_cache.invalidate()

    async def get_pool(self) -> list[LLMEndpoint]:
        """Returns all verified endpoints (a snapshot; see store/pool_cache.py)."""
        cached = self._pool_cache.get()
        if cached is not None:
            return cached
        generation = self._pool_cache.generation
        pool = await self.get_by_status(EndpointStatus.VERIFIED)
        self._pool_cache.put(pool, generation)
        return pool

    async def get_by_status(self, status: EndpointStatus) -> list[LLMEndpoint]:
        """Returns all endpoints with a specific status."""
        conn = await self._get_conn()
        async with conn.execute(
            "SELECT id, url, status, metadata, latency_ms, success_rate FROM endpoints WHERE status = ?",
            (status.value,),
        ) as cursor:
            return endpoints_from_rows(await cursor.fetchall())

    async def get_all(self) -> list[LLMEndpoint]:
        """Returns all endpoints in the database."""
        conn = await self._get_conn()
        async with conn.execute(
            "SELECT id, url, status, metadata, latency_ms, success_rate FROM endpoints"
        ) as cursor:
            return endpoints_from_rows(await cursor.fetchall())

    async def remove_endpoint(self, endpoint_id: str):
        async with self._write() as conn:
            await conn.execute("DELETE FROM endpoints WHERE id = ?", (endpoint_id,))
        self._pool_cache.invalidate()

    # App State Persistence
    async def set_state(self, key: str, value: Any):
        async with self._write() as conn:
            await conn.execute(
                "INSERT OR REPLACE INTO app_state (key, value) VALUES (?, ?)",
                (key, json.dumps(value)),
            )

    async def get_state(self, key: str, default: Any = None) -> Any:
        conn = await self._get_conn()
        async with conn.execute(
            "SELECT value FROM app_state WHERE key = ?", (key,)
        ) as cursor:
            row = await cursor.fetchone()
            return json.loads(row[0]) if row else default

    async def update_metrics(
        self, endpoint_id: str, latency_ms: float, success_rate: float
    ):
        """Updates latency and success rate for an endpoint."""
        async with self._write() as conn:
            await conn.execute(
                "UPDATE endpoints SET latency_ms = ?, success_rate = ? WHERE id = ?",
                (latency_ms, success_rate, endpoint_id),
            )
        self._pool_cache.invalidate()

    # ── Spend Log (R2.3) ──

    async def log_spend(
        self,
        ts: int,
        date: str,
        key_prefix: str,
        model: str,
        provider: str,
        prompt_tokens: int,
        completion_tokens: int,
        cost_usd: float,
        latency_ms: float,
        status: int,
    ):
        """Record a spend entry for analytics."""
        async with self._write() as conn:
            await conn.execute(
                "INSERT INTO spend_log (ts, date, key_prefix, model, provider, prompt_tokens, completion_tokens, cost_usd, latency_ms, status) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    ts,
                    date,
                    key_prefix,
                    model,
                    provider,
                    prompt_tokens,
                    completion_tokens,
                    cost_usd,
                    latency_ms,
                    status,
                ),
            )

    async def query_spend(
        self,
        date_from: str = "",
        date_to: str = "",
        group_by: str = "model",
        limit: int = 50,
    ) -> list:
        """Aggregate spend data grouped by model, provider, key, or date."""
        valid_groups = {"model", "provider", "key_prefix", "date"}
        col = group_by if group_by in valid_groups else "model"
        # Defensive: col is already validated above, but assert guards
        # against future maintainers expanding the whitelist carelessly.
        assert col in valid_groups, f"BUG: col '{col}' escaped whitelist"

        where = "WHERE 1=1"
        params: list = []
        if date_from:
            where += " AND date >= ?"
            params.append(date_from)
        if date_to:
            where += " AND date <= ?"
            params.append(date_to)

        sql = f"""
            SELECT {col},
                   COUNT(*) as requests,
                   SUM(prompt_tokens) as total_prompt_tokens,
                   SUM(completion_tokens) as total_completion_tokens,
                   SUM(cost_usd) as total_cost_usd,
                   AVG(latency_ms) as avg_latency_ms
            FROM spend_log {where}
            GROUP BY {col}
            ORDER BY total_cost_usd DESC
            LIMIT ?
        """
        params.append(limit)

        conn = await self._get_conn()
        async with self._row_factory_lock:
            conn.row_factory = aiosqlite.Row
            try:
                async with conn.execute(sql, params) as cursor:
                    rows = await cursor.fetchall()
                    result = [dict(r) for r in rows]
            finally:
                conn.row_factory = None
        return result

    async def get_spend_total(self, date_from: str = "", date_to: str = "") -> dict:
        """Get total spend summary."""
        where = "WHERE 1=1"
        params: list = []
        if date_from:
            where += " AND date >= ?"
            params.append(date_from)
        if date_to:
            where += " AND date <= ?"
            params.append(date_to)

        conn = await self._get_conn()
        async with conn.execute(
            f"SELECT COUNT(*) as requests, SUM(cost_usd) as total_usd, SUM(prompt_tokens) as total_prompt, SUM(completion_tokens) as total_completion FROM spend_log {where}",  # nosec B608
            params,
        ) as cursor:
            row = await cursor.fetchone()
            if row is None:
                return {
                    "requests": 0,
                    "total_usd": 0.0,
                    "total_prompt_tokens": 0,
                    "total_completion_tokens": 0,
                }
            return {
                "requests": row[0] or 0,
                "total_usd": round(row[1] or 0.0, 6),
                "total_prompt_tokens": row[2] or 0,
                "total_completion_tokens": row[3] or 0,
            }

    # ── Audit Log (R2.10) ──

    async def log_audit(
        self,
        ts: int,
        req_id: str,
        session_id: str,
        key_prefix: str,
        model: str,
        provider: str,
        status: int,
        prompt_tokens: int,
        completion_tokens: int,
        cost_usd: float,
        latency_ms: float,
        blocked: bool = False,
        block_reason: str = "",
        metadata: str = "{}",
    ):
        """Record an audit entry with hash chain for tamper detection.

        Each entry's hash includes the previous entry's hash, forming an
        append-only chain. If any entry is modified or deleted, the chain
        breaks and verify_audit_chain() will detect it.
        """
        import hashlib

        blocked_int = 1 if blocked else 0

        async with self._write() as conn:
            # Explicit, so another *process* writing the file cannot slip in
            # between reading the last hash and inserting the next row.
            await conn.execute("BEGIN IMMEDIATE")
            # Get the hash of the last entry (chain link)
            async with conn.execute(
                "SELECT entry_hash FROM audit_log ORDER BY id DESC LIMIT 1"
            ) as cursor:
                row = await cursor.fetchone()
                prev_hash = row[0] if row and row[0] else "GENESIS"

            # Compute deterministic hash: SHA256(prev_hash|ts|req_id|session_id|...)
            payload = (
                f"{prev_hash}|{ts}|{req_id}|{session_id}|{key_prefix}|"
                f"{model}|{provider}|{status}|{prompt_tokens}|{completion_tokens}|"
                f"{cost_usd}|{latency_ms}|{blocked_int}|{block_reason}|{metadata}"
            )
            entry_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()

            await conn.execute(
                "INSERT INTO audit_log (ts, req_id, session_id, key_prefix, model, provider, "
                "status, prompt_tokens, completion_tokens, cost_usd, latency_ms, blocked, "
                "block_reason, metadata, entry_hash, prev_hash) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    ts,
                    req_id,
                    session_id,
                    key_prefix,
                    model,
                    provider,
                    status,
                    prompt_tokens,
                    completion_tokens,
                    cost_usd,
                    latency_ms,
                    blocked_int,
                    block_reason,
                    metadata,
                    entry_hash,
                    prev_hash,
                ),
            )

    async def query_audit(
        self,
        date_from: str = "",
        date_to: str = "",
        model: str = "",
        key_prefix: str = "",
        status: int = 0,
        blocked: int = -1,
        limit: int = 100,
        offset: int = 0,
    ) -> dict:
        """Query audit log with filters."""
        where = "WHERE 1=1"
        params: list = []
        if date_from:
            from datetime import datetime

            ts_from = int(
                datetime.fromisoformat(date_from.replace("Z", "+00:00")).timestamp()
            )
            where += " AND ts >= ?"
            params.append(ts_from)
        if date_to:
            from datetime import datetime

            ts_to = int(
                datetime.fromisoformat(date_to.replace("Z", "+00:00")).timestamp()
            )
            where += " AND ts <= ?"
            params.append(ts_to)
        if model:
            where += " AND model = ?"
            params.append(model)
        if key_prefix:
            where += " AND key_prefix = ?"
            params.append(key_prefix)
        if status:
            where += " AND status = ?"
            params.append(status)
        if blocked >= 0:
            where += " AND blocked = ?"
            params.append(blocked)

        conn = await self._get_conn()
        # Count total
        async with conn.execute(f"SELECT COUNT(*) FROM audit_log {where}", params) as c:  # nosec B608
            _count_row = await c.fetchone()
            total = _count_row[0] if _count_row else 0

        # Fetch page
        async with self._row_factory_lock:
            conn.row_factory = aiosqlite.Row
            try:
                async with conn.execute(
                    f"SELECT * FROM audit_log {where} ORDER BY ts DESC LIMIT ? OFFSET ?",  # nosec B608
                    params + [limit, offset],
                ) as cursor:
                    rows = await cursor.fetchall()
                    items = [dict(r) for r in rows]
            finally:
                conn.row_factory = None

        return {"total": total, "items": items}

    # ── GDPR: Data Subject Rights ──

    async def _record_audit_gaps(self, conn, segments: list[dict]) -> None:
        """Add deletion records to app_state. Caller commits, in the same transaction."""
        if not segments:
            return
        async with conn.execute(
            "SELECT value FROM app_state WHERE key = ?", (audit_chain.GAPS_KEY,)
        ) as cursor:
            row = await cursor.fetchone()
        existing = audit_chain.load_gaps(json.loads(row[0]) if row else None)
        merged = audit_chain.merge_gaps(existing, segments)
        await conn.execute(
            "INSERT OR REPLACE INTO app_state (key, value) VALUES (?, ?)",
            (audit_chain.GAPS_KEY, json.dumps(merged)),
        )

    async def purge_expired(self, retention_days: int = 90) -> dict:
        """Delete audit/spend records older than retention_days.

        Audit rows are removed as the oldest *run* of the chain: everything
        before the first row that is still inside the window. The removed run's
        boundary hashes are recorded in the same transaction, so
        verify_audit_chain can tell this deletion from tampering. Holding the
        write lock keeps an append from reading a last-hash that the delete is
        about to take away.
        """
        import time

        cutoff_ts = int(time.time()) - (retention_days * 86400)

        async with self._write() as conn:
            async with conn.execute(
                "SELECT MIN(id) FROM audit_log WHERE ts >= ?", (cutoff_ts,)
            ) as cursor:
                row = await cursor.fetchone()
            first_kept = row[0] if row else None

            # Nothing inside the window: the whole chain is expired and the
            # next append starts a new one at GENESIS, so there is no
            # surviving row to bridge to.
            where, params = ("1=1", ()) if first_kept is None else ("id < ?", (first_kept,))

            async with conn.execute(
                f"SELECT prev_hash FROM audit_log WHERE {where} "
                "AND COALESCE(entry_hash, '') != '' ORDER BY id ASC LIMIT 1",
                params,
            ) as cursor:
                first = await cursor.fetchone()
            async with conn.execute(
                f"SELECT entry_hash FROM audit_log WHERE {where} "
                "AND COALESCE(entry_hash, '') != '' ORDER BY id DESC LIMIT 1",
                params,
            ) as cursor:
                last = await cursor.fetchone()

            cursor = await conn.execute(f"DELETE FROM audit_log WHERE {where}", params)
            audit_deleted = cursor.rowcount

            if first_kept is not None and first and last:
                await self._record_audit_gaps(
                    conn,
                    [
                        {
                            "start": first[0],
                            "end": last[0],
                            "rows": audit_deleted,
                            "reason": "retention",
                            "at": int(time.time()),
                        }
                    ],
                )

            cursor = await conn.execute(
                "DELETE FROM spend_log WHERE ts < ?", (cutoff_ts,)
            )
            spend_deleted = cursor.rowcount

        return {"audit_deleted": audit_deleted, "spend_deleted": spend_deleted}

    async def delete_subject_data(self, subject: str) -> dict:
        """Right to erasure: delete all data for a subject.

        Matches on session_id, key_prefix (audit/spend), and subject/email (user_roles).
        The subject's audit rows can sit anywhere in the chain; their boundary
        hashes are recorded in the same transaction so the rows around them
        still verify.
        """
        async with self._write() as conn:
            async with conn.execute(
                "SELECT id, prev_hash, entry_hash FROM audit_log "
                "WHERE session_id = ? OR key_prefix = ? ORDER BY id ASC",
                (subject, subject),
            ) as cursor:
                removed = [
                    {"id": r[0], "prev_hash": r[1] or "", "entry_hash": r[2] or ""}
                    for r in await cursor.fetchall()
                ]
            cursor = await conn.execute(
                "DELETE FROM audit_log WHERE session_id = ? OR key_prefix = ?",
                (subject, subject),
            )
            audit_deleted = cursor.rowcount
            await self._record_audit_gaps(
                conn, audit_chain.segments_from_rows(removed, reason="erasure")
            )

            cursor = await conn.execute(
                "DELETE FROM spend_log WHERE key_prefix = ?",
                (subject,),
            )
            spend_deleted = cursor.rowcount

            cursor = await conn.execute(
                "DELETE FROM user_roles WHERE subject = ? OR email = ?",
                (subject, subject),
            )
            roles_deleted = cursor.rowcount

        return {
            "audit_deleted": audit_deleted,
            "spend_deleted": spend_deleted,
            "roles_deleted": roles_deleted,
        }

    async def export_subject_data(self, subject: str) -> dict:
        """DSAR: export all data associated with a subject."""
        conn = await self._get_conn()
        async with self._row_factory_lock:
            conn.row_factory = aiosqlite.Row
            try:
                async with conn.execute(
                    "SELECT * FROM audit_log WHERE session_id = ? OR key_prefix = ? ORDER BY ts DESC",
                    (subject, subject),
                ) as cursor:
                    audit = [dict(r) for r in await cursor.fetchall()]

                async with conn.execute(
                    "SELECT * FROM spend_log WHERE key_prefix = ? ORDER BY ts DESC",
                    (subject,),
                ) as cursor:
                    spend = [dict(r) for r in await cursor.fetchall()]

                async with conn.execute(
                    "SELECT * FROM user_roles WHERE subject = ? OR email = ?",
                    (subject, subject),
                ) as cursor:
                    roles = [dict(r) for r in await cursor.fetchall()]
            finally:
                conn.row_factory = None
        return {"audit": audit, "spend": spend, "roles": roles}

    async def set_user_roles(
        self, subject: str, email: str | None, roles: list[str]
    ) -> None:
        import time

        now = int(time.time())
        async with self._write() as conn:
            await conn.execute("DELETE FROM user_roles WHERE subject = ?", (subject,))
            await conn.executemany(
                "INSERT INTO user_roles (subject, email, role, granted_at) VALUES (?,?,?,?)",
                [(subject, email or "", role, now) for role in roles],
            )

    async def get_user_roles(self, subject: str) -> list[str]:
        conn = await self._get_conn()
        async with conn.execute(
            "SELECT role FROM user_roles WHERE subject = ? ORDER BY id", (subject,)
        ) as cursor:
            return [row[0] for row in await cursor.fetchall()]

    async def get_audit_head(self) -> dict:
        """Id and hash of the newest audit row, and the row count, in one read."""
        conn = await self._get_conn()
        async with conn.execute(
            "SELECT id, entry_hash, (SELECT COUNT(*) FROM audit_log) "
            "FROM audit_log ORDER BY id DESC LIMIT 1"
        ) as cursor:
            row = await cursor.fetchone()
        return audit_chain.chain_head(tuple(row) if row else None)

    async def verify_audit_chain(self, anchor: dict | None = None) -> dict:
        """Verify the integrity of the audit log hash chain.

        Walks every entry in order, a page at a time, and recomputes its hash
        from the stored fields + previous hash. If any recomputed hash doesn't
        match the stored hash, the chain is broken (tamper detected). Rows
        removed by a recorded retention purge or erasure are bridged (see
        store/audit_chain.py). With ``anchor`` (``{"id", "hash"}``, a head
        recorded outside the database) the chain must also still contain that row.
        """
        conn = await self._get_conn()
        gaps = audit_chain.load_gaps(await self.get_state(audit_chain.GAPS_KEY))
        verifier = audit_chain.ChainVerifier(gaps, anchor)
        last_id = -1
        while True:
            # Keyset paging: no OFFSET, and rows appended meanwhile are simply
            # picked up by a later page.
            async with self._row_factory_lock:
                conn.row_factory = aiosqlite.Row
                try:
                    async with conn.execute(
                        "SELECT * FROM audit_log WHERE id > ? ORDER BY id ASC LIMIT ?",
                        (last_id, audit_chain.VERIFY_PAGE_SIZE),
                    ) as cursor:
                        rows = [dict(r) for r in await cursor.fetchall()]
                finally:
                    conn.row_factory = None
            if not rows:
                return verifier.result()
            failure = verifier.feed(rows)
            if failure is not None:
                return failure
            last_id = rows[-1]["id"]

    async def health_check(self) -> bool:
        """Verify the database connection is alive via a lightweight PRAGMA."""
        try:
            conn = await self._get_conn()
            async with conn.execute("PRAGMA quick_check(1)") as cur:
                row = await cur.fetchone()
                return row is not None and row[0] == "ok"
        except Exception as e:
            logger.error(f"SQLiteStore health check failed: {e}")
            # Connection is dead — close it (if possible) to release the fd,
            # then reset so _get_conn() recreates it on next call.
            old = self._conn
            self._conn = None
            if old is not None:
                try:
                    await old.close()
                except Exception:
                    pass  # already broken — swallow; the fd is released
            return False

    async def close(self):
        """Graceful shutdown — close persistent connection."""
        if self._conn:
            await self._conn.close()
            self._conn = None
            logger.info("SQLiteStore connection closed")

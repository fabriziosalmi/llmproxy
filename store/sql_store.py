import aiosqlite
import asyncio
import json
import logging
import os
import sqlite3

from .schema import MIGRATIONS, SQLITE, iter_create_statements
from typing import List, Dict, Any, Optional
from models import LLMEndpoint, EndpointStatus

logger = logging.getLogger(__name__)


class SQLiteStore:
    """Robust Asynchronous SQLite-based storage for LLM endpoints and metadata.

    Uses a single persistent connection (like CacheBackend) instead of
    opening a new connection per query.  All write operations are
    serialised through the connection's internal WAL lock; the explicit
    _audit_lock additionally guarantees hash-chain linearity for the
    audit log.
    """

    def __init__(self, db_path: str = "data/endpoints.db"):
        self.db_path = db_path
        self._conn: Optional[aiosqlite.Connection] = None
        self._conn_lock = asyncio.Lock()
        # Protects conn.row_factory mutations — row_factory is connection-level
        # in aiosqlite, so concurrent queries that toggle it would corrupt each
        # other's result types.
        self._row_factory_lock = asyncio.Lock()
        # Serialises concurrent log_audit calls so the hash chain is always
        # linear. Without this, two simultaneous requests read the same
        # prev_hash, compute diverging entry_hashes, and the chain splits —
        # verify_audit_chain() then reports permanent tamper-detection failure.
        self._audit_lock = asyncio.Lock()

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

    async def init_db(self):
        """Build the schema from the single declaration in store/schema.py.

        The CREATE statements used to live here in full, duplicated in
        store/pg_store.py in Postgres dialect and kept in step by hand. They
        had already drifted. Rendering them from one declaration means a
        column added for one backend is added for both.
        """
        conn = await self._get_conn()
        for stmt in iter_create_statements(SQLITE):
            await conn.execute(stmt)
        await self._run_migrations(conn)
        await conn.commit()

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
        conn = await self._get_conn()
        await conn.execute(
            "INSERT OR REPLACE INTO endpoints (id, url, status, metadata, latency_ms, success_rate) VALUES (?, ?, ?, ?, ?, ?)",
            (
                endpoint.id,
                str(endpoint.url),
                endpoint.status.value,
                json.dumps(endpoint.metadata),
                endpoint.latency_ms,
                endpoint.success_rate,
            ),
        )
        await conn.commit()

    async def update_status(
        self, endpoint_id: str, status: EndpointStatus, metadata: Optional[Dict] = None
    ):
        conn = await self._get_conn()
        # Extract latency_ms from metadata if present
        latency_ms = metadata.get("latency_ms") if metadata else None
        success_rate = metadata.get("success_rate") if metadata else None

        if metadata:
            await conn.execute(
                "UPDATE endpoints SET status = ?, metadata = ?, latency_ms = COALESCE(?, latency_ms), success_rate = COALESCE(?, success_rate), last_verified = CURRENT_TIMESTAMP WHERE id = ?",
                (
                    status.value,
                    json.dumps(metadata),
                    latency_ms,
                    success_rate,
                    endpoint_id,
                ),
            )
        else:
            await conn.execute(
                "UPDATE endpoints SET status = ?, last_verified = CURRENT_TIMESTAMP WHERE id = ?",
                (status.value, endpoint_id),
            )
        await conn.commit()

    async def get_pool(self) -> List[LLMEndpoint]:
        """Returns all verified endpoints."""
        return await self.get_by_status(EndpointStatus.VERIFIED)

    async def get_by_status(self, status: EndpointStatus) -> List[LLMEndpoint]:
        """Returns all endpoints with a specific status."""
        conn = await self._get_conn()
        async with conn.execute(
            "SELECT id, url, status, metadata, latency_ms, success_rate FROM endpoints WHERE status = ?",
            (status.value,),
        ) as cursor:
            rows = await cursor.fetchall()
            return [
                LLMEndpoint(
                    id=r[0],
                    url=r[1],
                    status=EndpointStatus(int(r[2])),
                    metadata=json.loads(r[3]),
                    latency_ms=r[4],
                    success_rate=r[5],
                )
                for r in rows
            ]

    async def get_all(self) -> List[LLMEndpoint]:
        """Returns all endpoints in the database."""
        conn = await self._get_conn()
        async with conn.execute(
            "SELECT id, url, status, metadata, latency_ms, success_rate FROM endpoints"
        ) as cursor:
            rows = await cursor.fetchall()
            return [
                LLMEndpoint(
                    id=r[0],
                    url=r[1],
                    status=EndpointStatus(int(r[2])),
                    metadata=json.loads(r[3]),
                    latency_ms=r[4],
                    success_rate=r[5],
                )
                for r in rows
            ]

    async def remove_endpoint(self, endpoint_id: str):
        conn = await self._get_conn()
        await conn.execute("DELETE FROM endpoints WHERE id = ?", (endpoint_id,))
        await conn.commit()

    # App State Persistence
    async def set_state(self, key: str, value: Any):
        conn = await self._get_conn()
        await conn.execute(
            "INSERT OR REPLACE INTO app_state (key, value) VALUES (?, ?)",
            (key, json.dumps(value)),
        )
        await conn.commit()

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
        conn = await self._get_conn()
        await conn.execute(
            "UPDATE endpoints SET latency_ms = ?, success_rate = ? WHERE id = ?",
            (latency_ms, success_rate, endpoint_id),
        )
        await conn.commit()

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
        conn = await self._get_conn()
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
        await conn.commit()

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

        async with self._audit_lock:
            conn = await self._get_conn()
            await conn.execute("BEGIN IMMEDIATE")
            try:
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
                await conn.commit()
            except Exception as e:
                try:
                    await conn.rollback()
                except Exception:
                    pass
                raise e

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

    async def purge_expired(self, retention_days: int = 90) -> dict:
        """Delete audit/spend records older than retention_days."""
        import time

        cutoff_ts = int(time.time()) - (retention_days * 86400)

        conn = await self._get_conn()
        cursor = await conn.execute("DELETE FROM audit_log WHERE ts < ?", (cutoff_ts,))
        audit_deleted = cursor.rowcount

        cursor = await conn.execute("DELETE FROM spend_log WHERE ts < ?", (cutoff_ts,))
        spend_deleted = cursor.rowcount

        await conn.commit()

        return {"audit_deleted": audit_deleted, "spend_deleted": spend_deleted}

    async def delete_subject_data(self, subject: str) -> dict:
        """Right to erasure: delete all data for a subject.

        Matches on session_id, key_prefix (audit/spend), and subject/email (user_roles).
        """
        conn = await self._get_conn()
        cursor = await conn.execute(
            "DELETE FROM audit_log WHERE session_id = ? OR key_prefix = ?",
            (subject, subject),
        )
        audit_deleted = cursor.rowcount

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

        await conn.commit()

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

    async def verify_audit_chain(self) -> dict:
        """Verify the integrity of the audit log hash chain.

        Walks every entry in order and recomputes its hash from the stored fields
        + previous hash. If any recomputed hash doesn't match the stored hash,
        the chain is broken (tamper detected).
        """
        import hashlib

        conn = await self._get_conn()
        # R2-12: Limit to last 100k rows to prevent OOM on large audit logs.
        # Verifying the most recent entries is sufficient for tamper detection.
        _MAX_VERIFY_ROWS = 100_000
        async with self._row_factory_lock:
            conn.row_factory = aiosqlite.Row
            try:
                async with conn.execute(
                    "SELECT * FROM audit_log ORDER BY id ASC LIMIT ?",
                    (_MAX_VERIFY_ROWS,),
                ) as cursor:
                    rows = [dict(r) for r in await cursor.fetchall()]
            finally:
                conn.row_factory = None

        expected_prev = "GENESIS"
        verified = 0

        for row in rows:
            stored_hash = row.get("entry_hash", "")
            stored_prev = row.get("prev_hash", "")

            # Blank entry_hash: tolerate ONLY for leading legacy rows written
            # before the hash-chain migration (no hashed row seen yet). A blank
            # hash AFTER hashed rows is an attacker blanking a row to truncate
            # the tail and re-anchor the chain to GENESIS — treat it as a break,
            # not a reset.
            if not stored_hash:
                if verified == 0:
                    expected_prev = "GENESIS"
                    continue
                return {
                    "valid": False,
                    "total": len(rows),
                    "verified": verified,
                    "broken_at": row.get("id"),
                    "error": f"blank entry_hash at id={row.get('id')} after hashed rows (tamper detected)",
                }

            # Verify prev_hash link
            if stored_prev != expected_prev:
                return {
                    "valid": False,
                    "total": len(rows),
                    "verified": verified,
                    "broken_at": row.get("id"),
                    "error": f"prev_hash mismatch at id={row.get('id')}",
                }

            # Recompute entry hash
            payload = (
                f"{stored_prev}|{row['ts']}|{row['req_id']}|{row['session_id']}|"
                f"{row['key_prefix']}|{row['model']}|{row['provider']}|{row['status']}|"
                f"{row['prompt_tokens']}|{row['completion_tokens']}|{row['cost_usd']}|"
                f"{row['latency_ms']}|{row['blocked']}|{row['block_reason']}|{row['metadata']}"
            )
            recomputed = hashlib.sha256(payload.encode("utf-8")).hexdigest()

            if recomputed != stored_hash:
                return {
                    "valid": False,
                    "total": len(rows),
                    "verified": verified,
                    "broken_at": row.get("id"),
                    "error": f"entry_hash mismatch at id={row.get('id')} (tamper detected)",
                }

            expected_prev = stored_hash
            verified += 1

        return {
            "valid": True,
            "total": len(rows),
            "verified": verified,
            "broken_at": None,
        }

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

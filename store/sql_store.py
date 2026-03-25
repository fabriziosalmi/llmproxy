import aiosqlite
import json
import logging
import sqlite3
from typing import List, Dict, Any, Optional
from models import LLMEndpoint, EndpointStatus

logger = logging.getLogger(__name__)

class SQLiteStore:
    """Robust Asynchronous SQLite-based storage for LLM endpoints and metadata."""

    def __init__(self, db_path: str = "endpoints.db"):
        self.db_path = db_path

    async def init_db(self):
        async with aiosqlite.connect(self.db_path) as conn:
            # WAL mode for concurrent read/write performance
            await conn.execute("PRAGMA journal_mode=WAL")
            await conn.execute("PRAGMA synchronous=NORMAL")
            await conn.execute("PRAGMA busy_timeout=5000")
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS endpoints (
                    id TEXT PRIMARY KEY,
                    url TEXT UNIQUE,
                    status INTEGER,
                    metadata TEXT,
                    last_verified TEXT,
                    latency_ms REAL,
                    success_rate REAL
                )
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS app_state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
            """)
            # Spend analytics log (R2.3)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS spend_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts INTEGER NOT NULL,
                    date TEXT NOT NULL,
                    key_prefix TEXT NOT NULL DEFAULT '',
                    model TEXT NOT NULL DEFAULT '',
                    provider TEXT NOT NULL DEFAULT '',
                    prompt_tokens INTEGER DEFAULT 0,
                    completion_tokens INTEGER DEFAULT 0,
                    cost_usd REAL DEFAULT 0.0,
                    latency_ms REAL DEFAULT 0.0,
                    status INTEGER DEFAULT 200
                )
            """)
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_spend_date ON spend_log(date)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_spend_model ON spend_log(model, date)")
            # Persistent audit log (R2.10)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts INTEGER NOT NULL,
                    req_id TEXT NOT NULL DEFAULT '',
                    session_id TEXT DEFAULT '',
                    key_prefix TEXT DEFAULT '',
                    model TEXT DEFAULT '',
                    provider TEXT DEFAULT '',
                    status INTEGER DEFAULT 200,
                    prompt_tokens INTEGER DEFAULT 0,
                    completion_tokens INTEGER DEFAULT 0,
                    cost_usd REAL DEFAULT 0.0,
                    latency_ms REAL DEFAULT 0.0,
                    blocked INTEGER DEFAULT 0,
                    block_reason TEXT DEFAULT '',
                    metadata TEXT DEFAULT '{}',
                    entry_hash TEXT DEFAULT '',
                    prev_hash TEXT DEFAULT ''
                )
            """)
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(ts)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_model ON audit_log(model)")
            # Migration: add hash columns to existing tables (no-op if already present)
            for col in ("entry_hash TEXT DEFAULT ''", "prev_hash TEXT DEFAULT ''"):
                try:
                    await conn.execute(f"ALTER TABLE audit_log ADD COLUMN {col}")
                except sqlite3.OperationalError:
                    pass  # Column already exists
            await conn.commit()

    async def add_endpoint(self, endpoint: LLMEndpoint):
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute(
                "INSERT OR REPLACE INTO endpoints (id, url, status, metadata, latency_ms, success_rate) VALUES (?, ?, ?, ?, ?, ?)",
                (endpoint.id, str(endpoint.url), endpoint.status.value, json.dumps(endpoint.metadata), endpoint.latency_ms, endpoint.success_rate)
            )
            await conn.commit()

    async def update_status(self, endpoint_id: str, status: EndpointStatus, metadata: Optional[Dict] = None):
        async with aiosqlite.connect(self.db_path) as conn:
            # Extract latency_ms from metadata if present
            latency_ms = metadata.get("latency_ms") if metadata else None
            success_rate = metadata.get("success_rate") if metadata else None

            if metadata:
                await conn.execute(
                    "UPDATE endpoints SET status = ?, metadata = ?, latency_ms = COALESCE(?, latency_ms), success_rate = COALESCE(?, success_rate), last_verified = CURRENT_TIMESTAMP WHERE id = ?",
                    (status.value, json.dumps(metadata), latency_ms, success_rate, endpoint_id)
                )
            else:
                await conn.execute(
                    "UPDATE endpoints SET status = ?, last_verified = CURRENT_TIMESTAMP WHERE id = ?",
                    (status.value, endpoint_id)
                )
            await conn.commit()

    async def get_pool(self) -> List[LLMEndpoint]:
        """Returns all verified endpoints."""
        return await self.get_by_status(EndpointStatus.VERIFIED)

    async def get_by_status(self, status: EndpointStatus) -> List[LLMEndpoint]:
        """Returns all endpoints with a specific status."""
        async with aiosqlite.connect(self.db_path) as conn:
            async with conn.execute("SELECT id, url, status, metadata, latency_ms, success_rate FROM endpoints WHERE status = ?", (status.value,)) as cursor:
                rows = await cursor.fetchall()
                return [
                    LLMEndpoint(
                        id=r[0], url=r[1], status=EndpointStatus(int(r[2])),
                        metadata=json.loads(r[3]), latency_ms=r[4], success_rate=r[5]
                    )
                    for r in rows
                ]

    async def get_all(self) -> List[LLMEndpoint]:
        """Returns all endpoints in the database."""
        async with aiosqlite.connect(self.db_path) as conn:
            async with conn.execute("SELECT id, url, status, metadata, latency_ms, success_rate FROM endpoints") as cursor:
                rows = await cursor.fetchall()
                return [
                    LLMEndpoint(
                        id=r[0], url=r[1], status=EndpointStatus(int(r[2])),
                        metadata=json.loads(r[3]), latency_ms=r[4], success_rate=r[5]
                    )
                    for r in rows
                ]

    async def remove_endpoint(self, endpoint_id: str):
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute("DELETE FROM endpoints WHERE id = ?", (endpoint_id,))
            await conn.commit()

    # App State Persistence
    async def set_state(self, key: str, value: Any):
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute(
                "INSERT OR REPLACE INTO app_state (key, value) VALUES (?, ?)",
                (key, json.dumps(value))
            )
            await conn.commit()

    async def get_state(self, key: str, default: Any = None) -> Any:
        async with aiosqlite.connect(self.db_path) as conn:
            async with conn.execute("SELECT value FROM app_state WHERE key = ?", (key,)) as cursor:
                row = await cursor.fetchone()
                return json.loads(row[0]) if row else default

    async def update_metrics(self, endpoint_id: str, latency_ms: float, success_rate: float):
        """Updates latency and success rate for an endpoint."""
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute(
                "UPDATE endpoints SET latency_ms = ?, success_rate = ? WHERE id = ?",
                (latency_ms, success_rate, endpoint_id)
            )
            await conn.commit()

    # ── Spend Log (R2.3) ──

    async def log_spend(self, ts: int, date: str, key_prefix: str, model: str,
                        provider: str, prompt_tokens: int, completion_tokens: int,
                        cost_usd: float, latency_ms: float, status: int):
        """Record a spend entry for analytics."""
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute(
                "INSERT INTO spend_log (ts, date, key_prefix, model, provider, prompt_tokens, completion_tokens, cost_usd, latency_ms, status) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (ts, date, key_prefix, model, provider, prompt_tokens, completion_tokens, cost_usd, latency_ms, status),
            )
            await conn.commit()

    async def query_spend(self, date_from: str = "", date_to: str = "",
                          group_by: str = "model", limit: int = 50) -> list:
        """Aggregate spend data grouped by model, provider, key, or date."""
        valid_groups = {"model", "provider", "key_prefix", "date"}
        col = group_by if group_by in valid_groups else "model"

        where = "WHERE 1=1"
        params = []
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

        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(sql, params) as cursor:
                rows = await cursor.fetchall()
                return [dict(r) for r in rows]

    async def get_spend_total(self, date_from: str = "", date_to: str = "") -> dict:
        """Get total spend summary."""
        where = "WHERE 1=1"
        params = []
        if date_from:
            where += " AND date >= ?"
            params.append(date_from)
        if date_to:
            where += " AND date <= ?"
            params.append(date_to)

        async with aiosqlite.connect(self.db_path) as conn:
            async with conn.execute(
                f"SELECT COUNT(*) as requests, SUM(cost_usd) as total_usd, SUM(prompt_tokens) as total_prompt, SUM(completion_tokens) as total_completion FROM spend_log {where}",
                params,
            ) as cursor:
                row = await cursor.fetchone()
                return {
                    "requests": row[0] or 0,
                    "total_usd": round(row[1] or 0.0, 6),
                    "total_prompt_tokens": row[2] or 0,
                    "total_completion_tokens": row[3] or 0,
                }

    # ── Audit Log (R2.10) ──

    async def log_audit(self, ts: int, req_id: str, session_id: str, key_prefix: str,
                        model: str, provider: str, status: int,
                        prompt_tokens: int, completion_tokens: int,
                        cost_usd: float, latency_ms: float,
                        blocked: bool = False, block_reason: str = "",
                        metadata: str = "{}"):
        """Record an audit entry with hash chain for tamper detection.

        Each entry's hash includes the previous entry's hash, forming an
        append-only chain. If any entry is modified or deleted, the chain
        breaks and verify_audit_chain() will detect it.
        """
        import hashlib
        blocked_int = 1 if blocked else 0

        async with aiosqlite.connect(self.db_path) as conn:
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
                (ts, req_id, session_id, key_prefix, model, provider, status,
                 prompt_tokens, completion_tokens, cost_usd, latency_ms,
                 blocked_int, block_reason, metadata, entry_hash, prev_hash),
            )
            await conn.commit()

    async def query_audit(self, date_from: str = "", date_to: str = "",
                          model: str = "", key_prefix: str = "",
                          status: int = 0, blocked: int = -1,
                          limit: int = 100, offset: int = 0) -> dict:
        """Query audit log with filters."""
        where = "WHERE 1=1"
        params = []
        if date_from:
            from datetime import datetime
            ts_from = int(datetime.fromisoformat(date_from.replace("Z", "+00:00")).timestamp())
            where += " AND ts >= ?"
            params.append(ts_from)
        if date_to:
            from datetime import datetime
            ts_to = int(datetime.fromisoformat(date_to.replace("Z", "+00:00")).timestamp())
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

        async with aiosqlite.connect(self.db_path) as conn:
            # Count total
            async with conn.execute(f"SELECT COUNT(*) FROM audit_log {where}", params) as c:
                total = (await c.fetchone())[0]

            # Fetch page
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                f"SELECT * FROM audit_log {where} ORDER BY ts DESC LIMIT ? OFFSET ?",
                params + [limit, offset],
            ) as cursor:
                rows = await cursor.fetchall()
                items = [dict(r) for r in rows]

        return {"total": total, "items": items}

    # ── GDPR: Data Subject Rights ──

    async def purge_expired(self, retention_days: int = 90) -> dict:
        """Delete audit/spend records older than retention_days."""
        import time
        cutoff_ts = int(time.time()) - (retention_days * 86400)

        async with aiosqlite.connect(self.db_path) as conn:
            cursor = await conn.execute(
                "DELETE FROM audit_log WHERE ts < ?", (cutoff_ts,)
            )
            audit_deleted = cursor.rowcount

            cursor = await conn.execute(
                "DELETE FROM spend_log WHERE ts < ?", (cutoff_ts,)
            )
            spend_deleted = cursor.rowcount

            await conn.commit()

        return {"audit_deleted": audit_deleted, "spend_deleted": spend_deleted}

    async def delete_subject_data(self, subject: str) -> dict:
        """Right to erasure: delete all data for a subject.

        Matches on session_id, key_prefix (audit/spend), and subject/email (user_roles).
        """
        async with aiosqlite.connect(self.db_path) as conn:
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
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row

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

        return {"audit": audit, "spend": spend, "roles": roles}

    async def verify_audit_chain(self) -> dict:
        """Verify the integrity of the audit log hash chain.

        Walks every entry in order and recomputes its hash from the stored fields
        + previous hash. If any recomputed hash doesn't match the stored hash,
        the chain is broken (tamper detected).
        """
        import hashlib

        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                "SELECT * FROM audit_log ORDER BY id ASC"
            ) as cursor:
                rows = [dict(r) for r in await cursor.fetchall()]

        if not rows:
            return {"valid": True, "total": 0, "verified": 0, "broken_at": None}

        expected_prev = "GENESIS"
        verified = 0

        for row in rows:
            stored_hash = row.get("entry_hash", "")
            stored_prev = row.get("prev_hash", "")

            # Skip legacy entries without hashes (pre-migration)
            if not stored_hash:
                expected_prev = "GENESIS"
                continue

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

        return {"valid": True, "total": len(rows), "verified": verified, "broken_at": None}

import asyncio
import json
import logging
import time as _time
from typing import List, Dict, Any, Optional
import asyncpg
from models import LLMEndpoint, EndpointStatus
from .base import BaseRepository

logger = logging.getLogger("llmproxy.store.pg")


class PostgresStore:
    """Robust Asynchronous PostgreSQL-based storage for LLM endpoints and metadata.

    Uses an asyncpg connection pool to handle concurrent operations safely.
    """

    def __init__(self, dsn: str):
        self.dsn = dsn
        self._pool: Optional[asyncpg.Pool] = None
        self._audit_lock = asyncio.Lock()

    async def init_pool(self):
        """Initialize the connection pool if not already initialized."""
        if self._pool is None:
            self._pool = await asyncpg.create_pool(self.dsn, min_size=2, max_size=20)
        return self._pool

    async def init_db(self):
        pool = await self.init_pool()
        async with pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS endpoints (
                    id VARCHAR(255) PRIMARY KEY,
                    url VARCHAR(512) UNIQUE,
                    status INTEGER,
                    metadata TEXT,
                    last_verified VARCHAR(50),
                    latency_ms DOUBLE PRECISION,
                    success_rate DOUBLE PRECISION
                )
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS app_state (
                    key VARCHAR(255) PRIMARY KEY,
                    value TEXT NOT NULL
                )
            """)
            # Spend analytics log
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS spend_log (
                    id SERIAL PRIMARY KEY,
                    ts BIGINT NOT NULL,
                    date VARCHAR(50) NOT NULL,
                    key_prefix VARCHAR(50) NOT NULL DEFAULT '',
                    model VARCHAR(255) NOT NULL DEFAULT '',
                    provider VARCHAR(100) NOT NULL DEFAULT '',
                    prompt_tokens INTEGER DEFAULT 0,
                    completion_tokens INTEGER DEFAULT 0,
                    cost_usd DOUBLE PRECISION DEFAULT 0.0,
                    latency_ms DOUBLE PRECISION DEFAULT 0.0,
                    status INTEGER DEFAULT 200
                )
            """)
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_spend_date ON spend_log(date)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_spend_model ON spend_log(model, date)"
            )

            # Persistent audit log
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS audit_log (
                    id SERIAL PRIMARY KEY,
                    ts BIGINT NOT NULL,
                    req_id VARCHAR(100) NOT NULL DEFAULT '',
                    session_id VARCHAR(100) DEFAULT '',
                    key_prefix VARCHAR(50) DEFAULT '',
                    model VARCHAR(255) DEFAULT '',
                    provider VARCHAR(100) DEFAULT '',
                    status INTEGER DEFAULT 200,
                    prompt_tokens INTEGER DEFAULT 0,
                    completion_tokens INTEGER DEFAULT 0,
                    cost_usd DOUBLE PRECISION DEFAULT 0.0,
                    latency_ms DOUBLE PRECISION DEFAULT 0.0,
                    blocked INTEGER DEFAULT 0,
                    block_reason TEXT DEFAULT '',
                    metadata TEXT DEFAULT '{}',
                    entry_hash VARCHAR(64) DEFAULT '',
                    prev_hash VARCHAR(64) DEFAULT ''
                )
            """)
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(ts)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_audit_model ON audit_log(model)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_audit_key ON audit_log(key_prefix, ts)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_audit_session ON audit_log(session_id, ts)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_spend_key ON spend_log(key_prefix, date)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_endpoints_status ON endpoints(status)"
            )

            # RBAC / GDPR: user_roles table
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS user_roles (
                    id SERIAL PRIMARY KEY,
                    subject VARCHAR(255) NOT NULL,
                    email VARCHAR(255) NOT NULL DEFAULT '',
                    role VARCHAR(100) NOT NULL DEFAULT '',
                    granted_at BIGINT DEFAULT 0
                )
            """)
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_user_roles_subject ON user_roles(subject)"
            )

            # Schema migration tracking
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS _migrations (
                    id SERIAL PRIMARY KEY,
                    name VARCHAR(255) UNIQUE NOT NULL,
                    applied_at BIGINT NOT NULL
                )
            """)

            # Run migrations (idempotent)
            _migrations = [
                (
                    "001_audit_hash_columns",
                    [
                        "ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS entry_hash VARCHAR(64) DEFAULT ''",
                        "ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS prev_hash VARCHAR(64) DEFAULT ''",
                    ],
                ),
            ]
            for mig_name, stmts in _migrations:
                row = await conn.fetchrow(
                    "SELECT 1 FROM _migrations WHERE name = $1", mig_name
                )
                if row:
                    continue
                for stmt in stmts:
                    try:
                        await conn.execute(stmt)
                    except Exception as e:
                        logger.debug(f"Postgres migration stmt skipped: {e}")
                await conn.execute(
                    "INSERT INTO _migrations (name, applied_at) VALUES ($1, $2)",
                    mig_name,
                    int(_time.time()),
                )

    async def add_endpoint(self, endpoint: LLMEndpoint):
        pool = await self.init_pool()
        await pool.execute(
            """
            INSERT INTO endpoints (id, url, status, metadata, latency_ms, success_rate)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (id) DO UPDATE SET
                url = EXCLUDED.url,
                status = EXCLUDED.status,
                metadata = EXCLUDED.metadata,
                latency_ms = EXCLUDED.latency_ms,
                success_rate = EXCLUDED.success_rate
            """,
            endpoint.id,
            str(endpoint.url),
            endpoint.status.value,
            json.dumps(endpoint.metadata),
            endpoint.latency_ms,
            endpoint.success_rate,
        )

    async def update_status(
        self, endpoint_id: str, status: EndpointStatus, metadata: Optional[Dict] = None
    ):
        pool = await self.init_pool()
        latency_ms = metadata.get("latency_ms") if metadata else None
        success_rate = metadata.get("success_rate") if metadata else None

        if metadata:
            await pool.execute(
                """
                UPDATE endpoints SET status = $1, metadata = $2,
                                     latency_ms = COALESCE($3, latency_ms),
                                     success_rate = COALESCE($4, success_rate),
                                     last_verified = TO_CHAR(NOW(), 'YYYY-MM-DD HH24:MI:SS')
                WHERE id = $5
                """,
                status.value,
                json.dumps(metadata),
                latency_ms,
                success_rate,
                endpoint_id,
            )
        else:
            await pool.execute(
                """
                UPDATE endpoints SET status = $1,
                                     last_verified = TO_CHAR(NOW(), 'YYYY-MM-DD HH24:MI:SS')
                WHERE id = $2
                """,
                status.value,
                endpoint_id,
            )

    async def get_by_status(self, status: EndpointStatus) -> List[LLMEndpoint]:
        pool = await self.init_pool()
        rows = await pool.fetch(
            "SELECT id, url, status, metadata, latency_ms, success_rate FROM endpoints WHERE status = $1",
            status.value,
        )
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
        pool = await self.init_pool()
        rows = await pool.fetch(
            "SELECT id, url, status, metadata, latency_ms, success_rate FROM endpoints"
        )
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
        pool = await self.init_pool()
        await pool.execute("DELETE FROM endpoints WHERE id = $1", endpoint_id)

    async def set_state(self, key: str, value: Any):
        pool = await self.init_pool()
        await pool.execute(
            """
            INSERT INTO app_state (key, value) VALUES ($1, $2)
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
            """,
            key,
            json.dumps(value),
        )

    async def get_state(self, key: str, default: Any = None) -> Any:
        pool = await self.init_pool()
        row = await pool.fetchrow("SELECT value FROM app_state WHERE key = $1", key)
        return json.loads(row[0]) if row else default

    async def update_metrics(
        self, endpoint_id: str, latency_ms: float, success_rate: float
    ):
        pool = await self.init_pool()
        await pool.execute(
            "UPDATE endpoints SET latency_ms = $1, success_rate = $2 WHERE id = $3",
            latency_ms,
            success_rate,
            endpoint_id,
        )

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
        pool = await self.init_pool()
        await pool.execute(
            """
            INSERT INTO spend_log (ts, date, key_prefix, model, provider, prompt_tokens,
                                   completion_tokens, cost_usd, latency_ms, status)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            """,
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
        )

    async def query_spend(
        self,
        date_from: str = "",
        date_to: str = "",
        group_by: str = "model",
        limit: int = 50,
    ) -> list:
        valid_groups = {"model", "provider", "key_prefix", "date"}
        col = group_by if group_by in valid_groups else "model"
        assert col in valid_groups, f"BUG: col '{col}' escaped whitelist"

        where = "WHERE 1=1"
        params: List[Any] = []
        param_counter = 1

        if date_from:
            where += f" AND date >= ${param_counter}"
            params.append(date_from)
            param_counter += 1
        if date_to:
            where += f" AND date <= ${param_counter}"
            params.append(date_to)
            param_counter += 1

        sql = f"""
            SELECT {col},
                   COUNT(*)::integer as requests,
                   SUM(prompt_tokens)::integer as total_prompt_tokens,
                   SUM(completion_tokens)::integer as total_completion_tokens,
                   SUM(cost_usd)::double precision as total_cost_usd,
                   AVG(latency_ms)::double precision as avg_latency_ms
            FROM spend_log {where}
            GROUP BY {col}
            ORDER BY total_cost_usd DESC
            LIMIT ${param_counter}
        """
        params.append(limit)

        pool = await self.init_pool()
        rows = await pool.fetch(sql, *params)
        return [dict(r) for r in rows]

    async def get_spend_total(self, date_from: str = "", date_to: str = "") -> dict:
        where = "WHERE 1=1"
        params: List[Any] = []
        param_counter = 1

        if date_from:
            where += f" AND date >= ${param_counter}"
            params.append(date_from)
            param_counter += 1
        if date_to:
            where += f" AND date <= ${param_counter}"
            params.append(date_to)
            param_counter += 1

        sql = f"""
            SELECT COUNT(*)::integer as requests,
                   SUM(cost_usd)::double precision as total_usd,
                   SUM(prompt_tokens)::integer as total_prompt,
                   SUM(completion_tokens)::integer as total_completion
            FROM spend_log {where}
        """

        pool = await self.init_pool()
        row = await pool.fetchrow(sql, *params)
        if row:
            return {
                "requests": row[0] or 0,
                "total_usd": round(row[1] or 0.0, 6),
                "total_prompt_tokens": row[2] or 0,
                "total_completion_tokens": row[3] or 0,
            }
        return {
            "requests": 0,
            "total_usd": 0.0,
            "total_prompt_tokens": 0,
            "total_completion_tokens": 0,
        }

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
        import hashlib

        blocked_int = 1 if blocked else 0

        async with self._audit_lock:
            pool = await self.init_pool()
            # Get the hash of the last entry (chain link)
            row = await pool.fetchrow(
                "SELECT entry_hash FROM audit_log ORDER BY id DESC LIMIT 1"
            )
            prev_hash = row[0] if row and row[0] else "GENESIS"

            # Compute deterministic hash
            payload = (
                f"{prev_hash}|{ts}|{req_id}|{session_id}|{key_prefix}|"
                f"{model}|{provider}|{status}|{prompt_tokens}|{completion_tokens}|"
                f"{cost_usd}|{latency_ms}|{blocked_int}|{block_reason}|{metadata}"
            )
            entry_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()

            await pool.execute(
                """
                INSERT INTO audit_log (ts, req_id, session_id, key_prefix, model, provider,
                                       status, prompt_tokens, completion_tokens, cost_usd, latency_ms, blocked,
                                       block_reason, metadata, entry_hash, prev_hash)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16)
                """,
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
        where = "WHERE 1=1"
        params: List[Any] = []
        param_counter = 1

        if date_from:
            from datetime import datetime

            ts_from = int(
                datetime.fromisoformat(date_from.replace("Z", "+00:00")).timestamp()
            )
            where += f" AND ts >= ${param_counter}"
            params.append(ts_from)
            param_counter += 1
        if date_to:
            from datetime import datetime

            ts_to = int(
                datetime.fromisoformat(date_to.replace("Z", "+00:00")).timestamp()
            )
            where += f" AND ts <= ${param_counter}"
            params.append(ts_to)
            param_counter += 1
        if model:
            where += f" AND model = ${param_counter}"
            params.append(model)
            param_counter += 1
        if key_prefix:
            where += f" AND key_prefix = ${param_counter}"
            params.append(key_prefix)
            param_counter += 1
        if status:
            where += f" AND status = ${param_counter}"
            params.append(status)
            param_counter += 1
        if blocked >= 0:
            where += f" AND blocked = ${param_counter}"
            params.append(blocked)
            param_counter += 1

        pool = await self.init_pool()
        total = await pool.fetchval(
            f"SELECT COUNT(*)::integer FROM audit_log {where}", *params
        )

        sql = f"""
            SELECT * FROM audit_log {where}
            ORDER BY ts DESC
            LIMIT ${param_counter} OFFSET ${param_counter + 1}
        """
        rows = await pool.fetch(sql, *(params + [limit, offset]))
        items = [dict(r) for r in rows]

        return {"total": total, "items": items}

    async def purge_expired(self, retention_days: int = 90) -> dict:
        import time

        cutoff_ts = int(time.time()) - (retention_days * 86400)

        pool = await self.init_pool()
        async with pool.acquire() as conn:
            # We run both deletes in a single connection transaction
            async with conn.transaction():
                audit_res = await conn.execute(
                    "DELETE FROM audit_log WHERE ts < $1", cutoff_ts
                )
                audit_deleted = int(audit_res.split(" ")[1]) if " " in audit_res else 0

                spend_res = await conn.execute(
                    "DELETE FROM spend_log WHERE ts < $1", cutoff_ts
                )
                spend_deleted = int(spend_res.split(" ")[1]) if " " in spend_res else 0

        return {"audit_deleted": audit_deleted, "spend_deleted": spend_deleted}

    async def delete_subject_data(self, subject: str) -> dict:
        pool = await self.init_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                r1 = await conn.execute(
                    "DELETE FROM audit_log WHERE session_id = $1 OR key_prefix = $2",
                    subject,
                    subject,
                )
                audit_deleted = int(r1.split(" ")[1]) if " " in r1 else 0

                r2 = await conn.execute(
                    "DELETE FROM spend_log WHERE key_prefix = $1", subject
                )
                spend_deleted = int(r2.split(" ")[1]) if " " in r2 else 0

                r3 = await conn.execute(
                    "DELETE FROM user_roles WHERE subject = $1 OR email = $2",
                    subject,
                    subject,
                )
                roles_deleted = int(r3.split(" ")[1]) if " " in r3 else 0

        return {
            "audit_deleted": audit_deleted,
            "spend_deleted": spend_deleted,
            "roles_deleted": roles_deleted,
        }

    async def export_subject_data(self, subject: str) -> dict:
        pool = await self.init_pool()
        audit_rows = await pool.fetch(
            "SELECT * FROM audit_log WHERE session_id = $1 OR key_prefix = $2 ORDER BY ts DESC",
            subject,
            subject,
        )
        spend_rows = await pool.fetch(
            "SELECT * FROM spend_log WHERE key_prefix = $1 ORDER BY ts DESC", subject
        )
        roles_rows = await pool.fetch(
            "SELECT * FROM user_roles WHERE subject = $1 OR email = $2",
            subject,
            subject,
        )

        return {
            "audit": [dict(r) for r in audit_rows],
            "spend": [dict(r) for r in spend_rows],
            "roles": [dict(r) for r in roles_rows],
        }

    async def verify_audit_chain(self) -> dict:
        import hashlib

        pool = await self.init_pool()
        _MAX_VERIFY_ROWS = 100_000
        rows = await pool.fetch(
            "SELECT * FROM audit_log ORDER BY id ASC LIMIT $1", _MAX_VERIFY_ROWS
        )

        expected_prev = "GENESIS"
        verified = 0

        for row in rows:
            stored_hash = row.get("entry_hash", "")
            stored_prev = row.get("prev_hash", "")

            if not stored_hash:
                expected_prev = "GENESIS"
                continue

            if stored_prev != expected_prev:
                return {
                    "valid": False,
                    "total": len(rows),
                    "verified": verified,
                    "broken_at": row.get("id"),
                    "error": f"prev_hash mismatch at id={row.get('id')}",
                }

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
        try:
            pool = await self.init_pool()
            val = await pool.fetchval("SELECT 1")
            return bool(val == 1)
        except Exception as e:
            logger.error(f"PostgresStore health check failed: {e}")
            old = self._pool
            self._pool = None
            if old is not None:
                try:
                    await old.close()
                except Exception:
                    pass
            return False

    async def close(self):
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
            logger.info("PostgresStore connection pool closed")


class PostgresRepository(BaseRepository):
    """PostgreSQL implementation of the LLMProxy repository."""

    def __init__(self, dsn: str):
        self.sql = PostgresStore(dsn)
        self.logger = logger

    async def init(self):
        await self.sql.init_db()
        self.logger.info("PostgresRepository initialized.")

    async def add_endpoint(self, endpoint: LLMEndpoint):
        await self.sql.add_endpoint(endpoint)

    async def remove_endpoint(self, endpoint_id: str):
        await self.sql.remove_endpoint(endpoint_id)

    async def get_all(self) -> List[LLMEndpoint]:
        return await self.sql.get_all()

    async def get_pool(self) -> List[LLMEndpoint]:
        return await self.sql.get_by_status(EndpointStatus.VERIFIED)

    async def get_by_status(self, status: EndpointStatus) -> List[LLMEndpoint]:
        return await self.sql.get_by_status(status)

    async def update_status(
        self, endpoint_id: str, status: EndpointStatus, metadata: Optional[Dict] = None
    ):
        await self.sql.update_status(endpoint_id, status, metadata)

    async def update_metrics(
        self, endpoint_id: str, latency_ms: float, success_rate: float
    ):
        await self.sql.update_metrics(endpoint_id, latency_ms, success_rate)

    async def set_state(self, key: str, value: Any):
        await self.sql.set_state(key, value)

    async def get_state(self, key: str, default: Any = None) -> Any:
        return await self.sql.get_state(key, default)

    async def log_spend(self, **kwargs):
        await self.sql.log_spend(**kwargs)

    async def query_spend(self, **kwargs):
        return await self.sql.query_spend(**kwargs)

    async def get_spend_total(self, **kwargs):
        return await self.sql.get_spend_total(**kwargs)

    async def log_audit(self, **kwargs):
        await self.sql.log_audit(**kwargs)

    async def query_audit(self, **kwargs):
        return await self.sql.query_audit(**kwargs)

    async def purge_expired(self, retention_days: int = 90) -> dict:
        return await self.sql.purge_expired(retention_days)

    async def delete_subject_data(self, subject: str) -> dict:
        return await self.sql.delete_subject_data(subject)

    async def export_subject_data(self, subject: str) -> dict:
        return await self.sql.export_subject_data(subject)

    async def verify_audit_chain(self) -> dict:
        return await self.sql.verify_audit_chain()

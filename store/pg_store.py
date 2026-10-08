import asyncio
import json
import logging
import time as _time
from typing import Any

import asyncpg

from models import EndpointStatus, LLMEndpoint, split_endpoint_stats

from . import audit_chain
from .base import BaseRepository
from .pool_cache import PoolCache
from .rows import endpoints_from_rows
from .schema import MIGRATIONS, POSTGRES, iter_create_statements

logger = logging.getLogger("llmproxy.store.pg")


class PostgresStore:
    """Robust Asynchronous PostgreSQL-based storage for LLM endpoints and metadata.

    Uses an asyncpg connection pool to handle concurrent operations safely.
    """

    def __init__(self, dsn: str):
        self.dsn = dsn
        self._pool: asyncpg.Pool | None = None
        self._audit_lock = asyncio.Lock()
        self._pool_cache = PoolCache()

    async def init_pool(self):
        """Initialize the connection pool if not already initialized."""
        if self._pool is None:
            self._pool = await asyncpg.create_pool(self.dsn, min_size=2, max_size=20)
        return self._pool

    async def init_db(self):
        """Build the schema from the single declaration in store/schema.py.

        The CREATE statements used to live here in full, duplicated in
        store/sql_store.py in SQLite dialect and kept in step by hand. They had
        already drifted. Rendering both from one declaration means a column
        added for one backend is added for both.
        """
        pool = await self.init_pool()
        async with pool.acquire() as conn:
            for stmt in iter_create_statements(POSTGRES):
                await conn.execute(stmt)
            await self._run_migrations(conn)

    async def _run_migrations(self, conn) -> None:
        """Apply pending migrations, recording only the ones that succeeded.

        This used to catch every Exception, log it at debug, and then record
        the migration as applied anyway — so a migration that failed for any
        reason was marked done, never retried, and its absence was invisible
        at the default log level.

        Postgres supports ADD COLUMN IF NOT EXISTS, so re-running a migration
        is already idempotent and there is nothing legitimate to swallow. A
        failure now propagates and aborts startup, which is the correct outcome
        for a database that could not be brought to the expected shape.
        """
        for mig_name, per_dialect in MIGRATIONS:
            row = await conn.fetchrow(
                "SELECT 1 FROM _migrations WHERE name = $1", mig_name
            )
            if row:
                continue
            for stmt in per_dialect[POSTGRES]:
                await conn.execute(stmt)
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
            json.dumps(split_endpoint_stats(endpoint.metadata)[0]),
            endpoint.latency_ms,
            endpoint.success_rate,
        )
        self._pool_cache.invalidate()

    async def update_status(
        self, endpoint_id: str, status: EndpointStatus, metadata: dict | None = None
    ):
        """See SQLiteStore.update_status: stats go to their columns only."""
        pool = await self.init_pool()
        clean, stats = split_endpoint_stats(metadata)
        latency_ms = stats.get("latency_ms")
        success_rate = stats.get("success_rate")

        if clean:
            await pool.execute(
                """
                UPDATE endpoints SET status = $1, metadata = $2,
                                     latency_ms = COALESCE($3, latency_ms),
                                     success_rate = COALESCE($4, success_rate),
                                     last_verified = TO_CHAR(NOW(), 'YYYY-MM-DD HH24:MI:SS')
                WHERE id = $5
                """,
                status.value,
                json.dumps(clean),
                latency_ms,
                success_rate,
                endpoint_id,
            )
        else:
            await pool.execute(
                """
                UPDATE endpoints SET status = $1,
                                     latency_ms = COALESCE($2, latency_ms),
                                     success_rate = COALESCE($3, success_rate),
                                     last_verified = TO_CHAR(NOW(), 'YYYY-MM-DD HH24:MI:SS')
                WHERE id = $4
                """,
                status.value,
                latency_ms,
                success_rate,
                endpoint_id,
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
        pool = await self.init_pool()
        rows = await pool.fetch(
            "SELECT id, url, status, metadata, latency_ms, success_rate FROM endpoints WHERE status = $1",
            status.value,
        )
        return endpoints_from_rows(rows)

    async def get_all(self) -> list[LLMEndpoint]:
        pool = await self.init_pool()
        rows = await pool.fetch(
            "SELECT id, url, status, metadata, latency_ms, success_rate FROM endpoints"
        )
        return endpoints_from_rows(rows)

    async def remove_endpoint(self, endpoint_id: str):
        pool = await self.init_pool()
        await pool.execute("DELETE FROM endpoints WHERE id = $1", endpoint_id)
        self._pool_cache.invalidate()

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
        self._pool_cache.invalidate()

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
        params: list[Any] = []
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
        params: list[Any] = []
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
            async with pool.acquire() as conn:
                async with conn.transaction():
                    # Acquire transaction-scoped advisory lock for hash chain serialization
                    await conn.execute("SELECT pg_advisory_xact_lock(987654321)")

                    # Get the hash of the last entry (chain link)
                    row = await conn.fetchrow(
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

                    await conn.execute(
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
        params: list[Any] = []
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

    #: Same key log_audit locks on, so a purge or erasure cannot interleave with
    #: an append from this process or any other replica.
    _AUDIT_ADVISORY_LOCK = 987654321

    async def _record_audit_gaps(self, conn, segments: list[dict]) -> None:
        """Add deletion records to app_state, inside the caller's transaction."""
        if not segments:
            return
        raw = await conn.fetchval(
            "SELECT value FROM app_state WHERE key = $1", audit_chain.GAPS_KEY
        )
        existing = audit_chain.load_gaps(json.loads(raw) if raw else None)
        merged = audit_chain.merge_gaps(existing, segments)
        await conn.execute(
            """
            INSERT INTO app_state (key, value) VALUES ($1, $2)
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
            """,
            audit_chain.GAPS_KEY,
            json.dumps(merged),
        )

    async def purge_expired(self, retention_days: int = 90) -> dict:
        """Delete audit/spend records older than retention_days.

        Audit rows go as the oldest run of the chain, and the run's boundary
        hashes are recorded in the same transaction so verify_audit_chain can
        tell this from tampering (see store/audit_chain.py).
        """
        cutoff_ts = int(_time.time()) - (retention_days * 86400)

        pool = await self.init_pool()
        async with self._audit_lock, pool.acquire() as conn:
            # Both deletes and the gap record commit together.
            async with conn.transaction():
                await conn.fetchval(
                    "SELECT pg_advisory_xact_lock($1)", self._AUDIT_ADVISORY_LOCK
                )
                first_kept = await conn.fetchval(
                    "SELECT MIN(id) FROM audit_log WHERE ts >= $1", cutoff_ts
                )
                # Nothing inside the window: the whole chain is expired and the
                # next append starts a new one at GENESIS.
                if first_kept is None:
                    where, params = "TRUE", []
                else:
                    where, params = "id < $1", [first_kept]

                first = await conn.fetchval(
                    f"SELECT prev_hash FROM audit_log WHERE {where} "
                    "AND COALESCE(entry_hash, '') != '' ORDER BY id ASC LIMIT 1",
                    *params,
                )
                last = await conn.fetchval(
                    f"SELECT entry_hash FROM audit_log WHERE {where} "
                    "AND COALESCE(entry_hash, '') != '' ORDER BY id DESC LIMIT 1",
                    *params,
                )

                audit_res = await conn.execute(f"DELETE FROM audit_log WHERE {where}", *params)
                audit_deleted = int(audit_res.split(" ")[1]) if " " in audit_res else 0

                if first_kept is not None and first and last:
                    await self._record_audit_gaps(
                        conn,
                        [
                            {
                                "start": first,
                                "end": last,
                                "rows": audit_deleted,
                                "reason": "retention",
                                "at": int(_time.time()),
                            }
                        ],
                    )

                spend_res = await conn.execute(
                    "DELETE FROM spend_log WHERE ts < $1", cutoff_ts
                )
                spend_deleted = int(spend_res.split(" ")[1]) if " " in spend_res else 0

        return {"audit_deleted": audit_deleted, "spend_deleted": spend_deleted}

    async def delete_subject_data(self, subject: str) -> dict:
        pool = await self.init_pool()
        async with self._audit_lock, pool.acquire() as conn:
            async with conn.transaction():
                await conn.fetchval(
                    "SELECT pg_advisory_xact_lock($1)", self._AUDIT_ADVISORY_LOCK
                )
                rows = await conn.fetch(
                    "SELECT id, prev_hash, entry_hash FROM audit_log "
                    "WHERE session_id = $1 OR key_prefix = $2 ORDER BY id ASC",
                    subject,
                    subject,
                )
                removed = [
                    {
                        "id": r["id"],
                        "prev_hash": r["prev_hash"] or "",
                        "entry_hash": r["entry_hash"] or "",
                    }
                    for r in rows
                ]
                r1 = await conn.execute(
                    "DELETE FROM audit_log WHERE session_id = $1 OR key_prefix = $2",
                    subject,
                    subject,
                )
                audit_deleted = int(r1.split(" ")[1]) if " " in r1 else 0
                await self._record_audit_gaps(
                    conn, audit_chain.segments_from_rows(removed, reason="erasure")
                )

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

    async def set_user_roles(
        self, subject: str, email: str | None, roles: list[str]
    ) -> None:
        import time

        pool = await self.init_pool()
        now = int(time.time())
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute("DELETE FROM user_roles WHERE subject = $1", subject)
            await conn.executemany(
                "INSERT INTO user_roles (subject, email, role, granted_at) "
                "VALUES ($1, $2, $3, $4)",
                [(subject, email or "", role, now) for role in roles],
            )

    async def get_user_roles(self, subject: str) -> list[str]:
        pool = await self.init_pool()
        rows = await pool.fetch(
            "SELECT role FROM user_roles WHERE subject = $1 ORDER BY id", subject
        )
        return [r[0] for r in rows]

    async def get_audit_head(self) -> dict:
        """Id and hash of the newest audit row, and the row count, in one read."""
        pool = await self.init_pool()
        row = await pool.fetchrow(
            "SELECT id, entry_hash, (SELECT COUNT(*) FROM audit_log) AS n "
            "FROM audit_log ORDER BY id DESC LIMIT 1"
        )
        return audit_chain.chain_head(tuple(row) if row else None)

    async def verify_audit_chain(self, anchor: dict | None = None) -> dict:
        """Verify the audit hash chain with the same rules as the SQLite store.

        Both backends share store.audit_chain.ChainVerifier and page through the
        whole chain by id. This one used to carry its own copy that reset the
        expected link on a blank entry_hash, the case the SQLite copy had been
        changed to treat as a break, and read only the first 100,000 rows.
        """
        pool = await self.init_pool()
        gaps = audit_chain.load_gaps(await self.get_state(audit_chain.GAPS_KEY))
        verifier = audit_chain.ChainVerifier(gaps, anchor)
        last_id = -1
        while True:
            rows = await pool.fetch(
                "SELECT * FROM audit_log WHERE id > $1 ORDER BY id ASC LIMIT $2",
                last_id,
                audit_chain.VERIFY_PAGE_SIZE,
            )
            if not rows:
                return verifier.result()
            page = [dict(r) for r in rows]
            failure = verifier.feed(page)
            if failure is not None:
                return failure
            last_id = page[-1]["id"]

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

    async def get_all(self) -> list[LLMEndpoint]:
        return await self.sql.get_all()

    async def get_pool(self) -> list[LLMEndpoint]:
        return await self.sql.get_pool()

    async def get_by_status(self, status: EndpointStatus) -> list[LLMEndpoint]:
        return await self.sql.get_by_status(status)

    async def update_status(
        self, endpoint_id: str, status: EndpointStatus, metadata: dict | None = None
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

    async def verify_audit_chain(self, anchor: dict | None = None) -> dict:
        return await self.sql.verify_audit_chain(anchor)

    async def get_audit_head(self) -> dict:
        return await self.sql.get_audit_head()

    async def set_user_roles(self, subject: str, email: str | None, roles: list[str]) -> None:
        await self.sql.set_user_roles(subject, email, roles)

    async def get_user_roles(self, subject: str) -> list[str]:
        return await self.sql.get_user_roles(subject)

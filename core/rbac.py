import asyncio
import logging
import sqlite3
from typing import Any

logger = logging.getLogger(__name__)

# Default role -> permission mapping
DEFAULT_PERMISSIONS: dict[str, set[str]] = {
    "admin": {
        "proxy:use",
        "proxy:toggle",
        "proxy:config",
        "registry:read",
        "registry:write",
        "registry:delete",
        "chat:use",
        "chat:compare",
        "logs:read",
        "logs:clear",
        "plugins:manage",
        "features:toggle",
        "users:manage",
        "budget:manage",
    },
    "operator": {
        "proxy:use",
        "proxy:toggle",
        "registry:read",
        "registry:write",
        "chat:use",
        "chat:compare",
        "logs:read",
        "logs:clear",
        "plugins:manage",
        "features:toggle",
    },
    "user": {
        "proxy:use",
        "registry:read",
        "chat:use",
        "logs:read",
    },
    "viewer": {
        "registry:read",
        "logs:read",
    },
}


class RBACManager:
    """Manages API Key quotas, budgets, and role-based access.

    Uses aiosqlite connection sharing for fast, non-blocking async operations.
    """

    #: A subject's roles are written again after this long even when unchanged.
    #: It bounds the writes (set_user_roles runs on every identity-authenticated
    #: request) while letting an erased subject be recorded afresh on next use.
    ROLE_REFRESH_SECONDS = 300
    _ROLE_CACHE_MAX = 10_000

    def __init__(self, db_path: str = "endpoints.db", store: Any | None = None):
        """``db_path`` holds the quota table. Roles are kept by ``store`` (the
        repository), in the user_roles table that erasure and export read."""
        self.db_path = db_path
        self._store = store
        self._role_written: dict[str, tuple[str | None, tuple[str, ...], float]] = {}
        self.permissions = dict(DEFAULT_PERMISSIONS)
        self._conn: Any | None = None
        self._conn_lock = asyncio.Lock()
        self._sync_init_db()

    def _sync_init_db(self):
        """Sync init -- only called from __init__ (before event loop starts)."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS quotas (
                    api_key TEXT PRIMARY KEY,
                    team_name TEXT,
                    monthly_budget REAL,
                    consumed_budget REAL DEFAULT 0.0,
                    hard_limit BOOLEAN DEFAULT 1
                )
            """)
            conn.commit()

    async def _get_conn(self):
        if not self._conn:
            async with self._conn_lock:
                if not self._conn:
                    import aiosqlite
                    self._conn = await aiosqlite.connect(self.db_path)
                    self._conn.row_factory = aiosqlite.Row
                    await self._conn.execute("PRAGMA journal_mode=WAL")
                    await self._conn.execute("PRAGMA synchronous=NORMAL")
        return self._conn

    async def close(self):
        """Close shared database connection."""
        if self._conn:
            await self._conn.close()
            self._conn = None

    async def check_quota(self, api_key: str) -> bool:
        """Returns True if the API key has remaining budget."""
        conn = await self._get_conn()
        async with conn.execute(
            "SELECT monthly_budget, consumed_budget, hard_limit FROM quotas WHERE api_key = ?",
            (api_key,),
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return True
            budget, consumed, hard_limit = row[0], row[1], row[2]
            if hard_limit and consumed >= budget:
                logger.warning(
                    f"RBAC: Quota exceeded ({consumed}/{budget})"
                )
                return False
            return True

    async def update_usage(self, api_key: str, cost: float):
        """Increments the consumed budget for an API key."""
        conn = await self._get_conn()
        await conn.execute(
            "UPDATE quotas SET consumed_budget = consumed_budget + ? WHERE api_key = ?",
            (cost, api_key),
        )
        await conn.commit()

    def add_quota(self, api_key: str, team: str, budget: float):
        """Configures a quota for a new or existing key."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO quotas (api_key, team_name, monthly_budget) VALUES (?, ?, ?)",
                (api_key, team, budget),
            )
            conn.commit()

    # -- Role-based permission checks (pure in-memory, no I/O) --

    def check_permission(self, roles: list[str], permission: str) -> bool:
        """Check if any of the given roles grants the specified permission."""
        for role in roles:
            role_perms = self.permissions.get(role, set())
            if permission in role_perms:
                return True
        return False

    def get_permissions_for_roles(self, roles: list[str]) -> set[str]:
        """Get the union of all permissions for the given roles."""
        perms: set[str] = set()
        for role in roles:
            perms |= self.permissions.get(role, set())
        return perms

    async def set_user_roles(
        self, subject: str, email: str | None, roles: list[str]
    ):
        """Persist user->role mapping (in the repository's user_roles table).

        This used to write a ``user_roles`` table of its own, with a different
        shape, into ``endpoints.db`` relative to the working directory: not the
        store's file, not the data volume, not backed up, and not the table GDPR
        export and erasure read. Both returned empty for a user the proxy had
        recorded, and erasure reported zero roles deleted.
        """
        import time

        store = self._require_store()
        key = (email, tuple(roles))
        previous = self._role_written.get(subject)
        now = time.monotonic()
        if (
            previous
            and previous[:2] == key
            and now - previous[2] < self.ROLE_REFRESH_SECONDS
        ):
            return
        await store.set_user_roles(subject, email, roles)
        if len(self._role_written) >= self._ROLE_CACHE_MAX:
            self._role_written.clear()
        self._role_written[subject] = (*key, now)

    def _require_store(self) -> Any:
        if self._store is None:
            raise RuntimeError(
                "RBACManager was built without a store; roles are kept by the "
                "repository. Pass RBACManager(store=...)."
            )
        return self._store

    async def get_user_roles(self, subject: str) -> list[str]:
        """Look up persisted roles for a user subject ("user" when none)."""
        roles = await self._require_store().get_user_roles(subject)
        return roles or ["user"]

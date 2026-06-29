import sqlite3
import asyncio
import logging
from typing import Dict, Optional, List, Set, Any

logger = logging.getLogger(__name__)

# Default role -> permission mapping
DEFAULT_PERMISSIONS: Dict[str, Set[str]] = {
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

    def __init__(self, db_path: str = "endpoints.db"):
        self.db_path = db_path
        self.permissions = dict(DEFAULT_PERMISSIONS)
        self._conn: Optional[Any] = None
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
            conn.execute("""
                CREATE TABLE IF NOT EXISTS user_roles (
                    subject TEXT PRIMARY KEY,
                    email TEXT,
                    roles TEXT DEFAULT 'user',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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

    def check_permission(self, roles: List[str], permission: str) -> bool:
        """Check if any of the given roles grants the specified permission."""
        for role in roles:
            role_perms = self.permissions.get(role, set())
            if permission in role_perms:
                return True
        return False

    def get_permissions_for_roles(self, roles: List[str]) -> Set[str]:
        """Get the union of all permissions for the given roles."""
        perms: Set[str] = set()
        for role in roles:
            perms |= self.permissions.get(role, set())
        return perms

    async def set_user_roles(
        self, subject: str, email: Optional[str], roles: List[str]
    ):
        """Persist user->role mapping."""
        conn = await self._get_conn()
        await conn.execute(
            "INSERT OR REPLACE INTO user_roles (subject, email, roles) VALUES (?, ?, ?)",
            (subject, email, ",".join(roles)),
        )
        await conn.commit()

    async def get_user_roles(self, subject: str) -> List[str]:
        """Look up persisted roles for a user subject."""
        conn = await self._get_conn()
        async with conn.execute(
            "SELECT roles FROM user_roles WHERE subject = ?", (subject,)
        ) as cursor:
            row = await cursor.fetchone()
            if row and row[0]:
                return [r.strip() for r in row[0].split(",") if r.strip()]
        return ["user"]

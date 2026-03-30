import sqlite3
import asyncio
import logging
from typing import Dict, Optional, List, Set

logger = logging.getLogger(__name__)

# Default role -> permission mapping
DEFAULT_PERMISSIONS: Dict[str, Set[str]] = {
    "admin": {
        "proxy:use", "proxy:toggle", "proxy:config",
        "registry:read", "registry:write", "registry:delete",
        "chat:use", "chat:compare",
        "logs:read", "logs:clear",
        "plugins:manage", "features:toggle",
        "users:manage", "budget:manage",
    },
    "operator": {
        "proxy:use", "proxy:toggle",
        "registry:read", "registry:write",
        "chat:use", "chat:compare",
        "logs:read", "logs:clear",
        "plugins:manage", "features:toggle",
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

    All SQLite operations are wrapped in asyncio.to_thread() to avoid
    blocking the event loop. Sync variants (_sync_*) exist for __init__.
    """

    def __init__(self, db_path: str = "endpoints.db"):
        self.db_path = db_path
        self.permissions = dict(DEFAULT_PERMISSIONS)
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

    def _sync_check_quota(self, api_key: str) -> bool:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT monthly_budget, consumed_budget, hard_limit FROM quotas WHERE api_key = ?",
                (api_key,)
            )
            row = cursor.fetchone()
            if not row:
                return True
            budget, consumed, hard_limit = row
            if hard_limit and consumed >= budget:
                logger.warning(f"RBAC: Key {api_key[:8]}... exceeded budget ({consumed}/{budget})")
                return False
            return True

    async def check_quota(self, api_key: str) -> bool:
        """Returns True if the API key has remaining budget.

        Runs the SQLite read in a thread-pool worker so it never blocks the
        asyncio event loop, which would stall ALL concurrent requests.
        """
        return await asyncio.to_thread(self._sync_check_quota, api_key)

    def _sync_update_usage(self, api_key: str, cost: float):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE quotas SET consumed_budget = consumed_budget + ? WHERE api_key = ?",
                (cost, api_key)
            )
            conn.commit()

    async def update_usage(self, api_key: str, cost: float):
        """Increments the consumed budget for an API key (non-blocking)."""
        await asyncio.to_thread(self._sync_update_usage, api_key, cost)

    def add_quota(self, api_key: str, team: str, budget: float):
        """Configures a quota for a new or existing key."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO quotas (api_key, team_name, monthly_budget) VALUES (?, ?, ?)",
                (api_key, team, budget)
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

    def _sync_set_user_roles(self, subject: str, email: Optional[str], roles: List[str]):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO user_roles (subject, email, roles) VALUES (?, ?, ?)",
                (subject, email, ",".join(roles))
            )
            conn.commit()

    async def set_user_roles(self, subject: str, email: Optional[str], roles: List[str]):
        """Persist user->role mapping (non-blocking).

        Runs the SQLite write in a thread-pool worker — calling sqlite3 directly
        on the asyncio event loop stalls ALL concurrent requests.
        """
        await asyncio.to_thread(self._sync_set_user_roles, subject, email, roles)

    def _sync_get_user_roles(self, subject: str) -> List[str]:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT roles FROM user_roles WHERE subject = ?", (subject,)
            )
            row = cursor.fetchone()
            if row and row[0]:
                return [r.strip() for r in row[0].split(",") if r.strip()]
        return ["user"]

    async def get_user_roles(self, subject: str) -> List[str]:
        """Look up persisted roles for a user subject (non-blocking)."""
        return await asyncio.to_thread(self._sync_get_user_roles, subject)

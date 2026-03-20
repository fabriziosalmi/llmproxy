import sqlite3
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

class RBACManager:
    """Manages API Key quotas, budgets, and role-based access."""
    
    def __init__(self, db_path: str = "endpoints.db"):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
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

    def check_quota(self, api_key: str) -> bool:
        """Returns True if the API key has remaining budget."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT monthly_budget, consumed_budget, hard_limit FROM quotas WHERE api_key = ?",
                (api_key,)
            )
            row = cursor.fetchone()
            if not row:
                return True # Default to allow if not in quota table
                
            budget, consumed, hard_limit = row
            if hard_limit and consumed >= budget:
                logger.warning(f"RBAC: Key {api_key[:8]}... exceeded budget ({consumed}/{budget})")
                return False
            return True

    def update_usage(self, api_key: str, cost: float):
        """Increments the consumed budget for an API key."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE quotas SET consumed_budget = consumed_budget + ? WHERE api_key = ?",
                (cost, api_key)
            )
            conn.commit()

    def add_quota(self, api_key: str, team: str, budget: float):
        """Configures a quota for a new or existing key."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO quotas (api_key, team_name, monthly_budget) VALUES (?, ?, ?)",
                (api_key, team, budget)
            )
            conn.commit()

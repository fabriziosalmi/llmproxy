import aiosqlite
import json
import logging
from typing import List, Dict, Any, Optional
from models import LLMEndpoint, EndpointStatus

logger = logging.getLogger(__name__)

class SQLiteStore:
    """Robust Asynchronous SQLite-based storage for LLM endpoints and metadata."""
    
    def __init__(self, db_path: str = "endpoints.db"):
        self.db_path = db_path

    async def init_db(self):
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS endpoints (
                    id TEXT PRIMARY KEY,
                    url TEXT NOT NULL,
                    status TEXT NOT NULL,
                    metadata TEXT,
                    last_verified TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS app_state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
            """)
            await conn.commit()

    async def add_endpoint(self, endpoint: LLMEndpoint):
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute(
                "INSERT OR REPLACE INTO endpoints (id, url, status, metadata) VALUES (?, ?, ?, ?)",
                (endpoint.id, str(endpoint.url), endpoint.status.value, json.dumps(endpoint.metadata))
            )
            await conn.commit()

    async def update_status(self, endpoint_id: str, status: EndpointStatus, metadata: Optional[Dict] = None):
        async with aiosqlite.connect(self.db_path) as conn:
            if metadata:
                await conn.execute(
                    "UPDATE endpoints SET status = ?, metadata = ?, last_verified = CURRENT_TIMESTAMP WHERE id = ?",
                    (status.value, json.dumps(metadata), endpoint_id)
                )
            else:
                await conn.execute(
                    "UPDATE endpoints SET status = ?, last_verified = CURRENT_TIMESTAMP WHERE id = ?",
                    (status.value, endpoint_id)
                )
            await conn.commit()

    async def get_pool(self) -> List[LLMEndpoint]:
        """Returns all verified endpoints."""
        async with aiosqlite.connect(self.db_path) as conn:
            async with conn.execute("SELECT id, url, status, metadata FROM endpoints WHERE status = ?", (EndpointStatus.VERIFIED.value,)) as cursor:
                rows = await cursor.fetchall()
                return [
                    LLMEndpoint(id=r[0], url=r[1], status=EndpointStatus(r[2]), metadata=json.loads(r[3]))
                    for r in rows
                ]

    async def get_all(self) -> List[LLMEndpoint]:
        """Returns all endpoints in the database."""
        async with aiosqlite.connect(self.db_path) as conn:
            async with conn.execute("SELECT id, url, status, metadata FROM endpoints") as cursor:
                rows = await cursor.fetchall()
                return [
                    LLMEndpoint(id=r[0], url=r[1], status=EndpointStatus(r[2]), metadata=json.loads(r[3]))
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

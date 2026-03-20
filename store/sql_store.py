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

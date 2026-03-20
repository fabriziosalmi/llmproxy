import logging
from typing import List, Optional, Dict, Any
from models import LLMEndpoint, EndpointStatus
from .sql_store import SQLiteStore
from .base import BaseRepository

logger = logging.getLogger(__name__)

class SQLiteRepository(BaseRepository):
    """SQLite implementation of the LLMProxy repository."""
    
    def __init__(self, db_path: str = "endpoints.db"):
        self.sql = SQLiteStore(db_path)
        self.logger = logger

    async def init(self):
        """Async initialization for SQLite."""
        await self.sql.init_db()
        self.logger.info("SQLiteRepository (Async) initialized.")

    async def add_endpoint(self, endpoint: LLMEndpoint):
        await self.sql.add_endpoint(endpoint)

    async def remove_endpoint(self, endpoint_id: str):
        await self.sql.remove_endpoint(endpoint_id)

    async def get_all(self) -> List[LLMEndpoint]:
        return await self.sql.get_all()

    async def get_pool(self) -> List[LLMEndpoint]:
        return await self.sql.get_pool()

    async def update_status(self, endpoint_id: str, status: EndpointStatus, metadata: Optional[Dict] = None):
        await self.sql.update_status(endpoint_id, status, metadata)

    async def update_metrics(self, endpoint_id: str, latency_ms: float, success_rate: float):
        await self.sql.update_metrics(endpoint_id, latency_ms, success_rate)

    async def set_state(self, key: str, value: Any):
        await self.sql.set_state(key, value)

    async def get_state(self, key: str, default: Any = None) -> Any:
        return await self.sql.get_state(key, default)

# Legacy alias for backward compatibility during refactor
EndpointStore = SQLiteRepository

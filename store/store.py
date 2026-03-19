import logging
import os
from typing import List, Optional, Dict, Any
from models import LLMEndpoint, EndpointStatus
from .sql_store import SQLiteStore

logger = logging.getLogger(__name__)

class EndpointStore:
    def __init__(self, db_path: str = "endpoints.db"):
        self.sql = SQLiteStore(db_path)
        self.logger = logger

    async def init(self):
        """Async initialization to create tables."""
        await self.sql.init_db()
        self.logger.info("EndpointStore (Async) initialized with SQLite backend.")

    async def add_endpoint(self, endpoint: LLMEndpoint):
        await self.sql.add_endpoint(endpoint)

    async def update_status(self, endpoint_id: str, status: EndpointStatus, metadata: Dict = None):
        await self.sql.update_status(endpoint_id, status, metadata)

    async def get_pool(self) -> List[LLMEndpoint]:
        return await self.sql.get_pool()

    async def get_all(self) -> List[LLMEndpoint]:
        return await self.sql.get_all()

    async def remove_endpoint(self, endpoint_id: str):
        await self.sql.remove_endpoint(endpoint_id)

    # State persistence
    async def set_state(self, key: str, value: Any):
        await self.sql.set_state(key, value)

    async def get_state(self, key: str, default: Any = None) -> Any:
        return await self.sql.get_state(key, default)

import logging
from typing import Any

from models import EndpointStatus, LLMEndpoint

from .base import BaseRepository
from .sql_store import SQLiteStore

logger = logging.getLogger(__name__)


class SQLiteRepository(BaseRepository):
    """SQLite implementation of the LLMProxy repository."""

    def __init__(self, db_path: str = "data/endpoints.db"):
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

    # ── Spend Log (R2.3) ──

    async def log_spend(self, **kwargs):
        await self.sql.log_spend(**kwargs)

    async def query_spend(self, **kwargs):
        return await self.sql.query_spend(**kwargs)

    async def get_spend_total(self, **kwargs):
        return await self.sql.get_spend_total(**kwargs)

    # ── Audit Log (R2.10) ──

    async def log_audit(self, **kwargs):
        await self.sql.log_audit(**kwargs)

    async def query_audit(self, **kwargs):
        return await self.sql.query_audit(**kwargs)

    # ── GDPR and audit integrity ──
    #
    # These four were missing here, so SQLiteRepository (the default backend,
    # and the one StorageFactory builds) inherited BaseRepository's stubs: the
    # retention purge deleted nothing, erasure reported "no data" and erased
    # nothing, the access request exported nothing, and verify_audit_chain said
    # "valid" for a tampered chain. PostgresRepository had them, which is why
    # nothing noticed. tests/test_repository_contract.py now fails if a concrete
    # repository leaves any of them to the base class.

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

    async def close(self):
        """Close the underlying SQLite store connection."""
        await self.sql.close()


# Legacy alias for backward compatibility during refactor
EndpointStore = SQLiteRepository

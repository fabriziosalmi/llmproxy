from abc import ABC, abstractmethod
from typing import Any, Protocol, runtime_checkable

from models import EndpointStatus, LLMEndpoint


@runtime_checkable
class StateBackend(Protocol):
    """Structural protocol for key-value state storage.

    Any object implementing set_state/get_state satisfies this protocol
    without inheritance — enables drop-in Redis, DragonflyDB, or Postgres
    replacements without touching BaseRepository.
    """

    async def set_state(self, key: str, value: Any) -> None: ...
    async def get_state(self, key: str, default: Any = None) -> Any: ...


class BaseRepository(ABC):
    """Abstract base class for LLMProxy storage backends."""

    @abstractmethod
    async def init(self):
        """Initializes the storage backend (e.g., connect, create tables)."""
        pass

    @abstractmethod
    async def add_endpoint(self, endpoint: LLMEndpoint):
        """Adds a new LLM endpoint to the registry."""
        pass

    @abstractmethod
    async def remove_endpoint(self, endpoint_id: str):
        """Removes an endpoint from the registry."""
        pass

    @abstractmethod
    async def get_all(self) -> list[LLMEndpoint]:
        """Returns all registered endpoints."""
        pass

    @abstractmethod
    async def get_pool(self) -> list[LLMEndpoint]:
        """Returns only 'VERIFIED' and 'Live' endpoints."""
        pass

    @abstractmethod
    async def get_by_status(self, status: EndpointStatus) -> list[LLMEndpoint]:
        """Returns endpoints filtered by their status."""
        pass

    @abstractmethod
    async def update_status(
        self, endpoint_id: str, status: EndpointStatus, metadata: dict | None = None
    ):
        """Updates the status and metadata of an endpoint."""
        pass

    @abstractmethod
    async def update_metrics(
        self, endpoint_id: str, latency_ms: float, success_rate: float
    ):
        """Updates performance metrics for an endpoint."""
        pass

    @abstractmethod
    async def set_state(self, key: str, value: Any):
        """Sets a system-wide state value (e.g., proxy_enabled)."""
        pass

    @abstractmethod
    async def get_state(self, key: str, default: Any = None) -> Any:
        """Retrieves a system-wide state value."""
        pass

    # ── Spend & Audit Logging ──
    # Default no-op implementations — subclasses with SQLite override these.

    @abstractmethod
    async def log_spend(self, **kwargs):
        """Record a spend entry. No-op without persistent storage."""
        pass

    @abstractmethod
    async def log_audit(self, **kwargs):
        """Record an audit entry. No-op without persistent storage."""
        pass

    async def query_spend(self, **kwargs) -> list:
        """Query spend data. Returns empty without persistent storage."""
        return []

    async def get_spend_total(self, **kwargs) -> dict:
        """Get spend totals. Returns zeros without persistent storage."""
        return {
            "requests": 0,
            "total_usd": 0.0,
            "total_prompt_tokens": 0,
            "total_completion_tokens": 0,
        }

    async def query_audit(self, **kwargs) -> dict:
        """Query audit log. Returns empty without persistent storage."""
        return {"total": 0, "items": []}

    # ── GDPR: Data Subject Rights ──

    # A repository that stores audit data MUST implement these. The defaults used
    # to return empty results, so a backend that forgot one (SQLiteRepository did)
    # silently did nothing: no purge, no erasure, an empty export and an audit
    # chain that always verified. A missing implementation now raises.

    async def purge_expired(self, retention_days: int = 90) -> dict:
        """Delete audit/spend records older than retention_days. Returns counts."""
        raise NotImplementedError(f"{type(self).__name__} does not implement purge_expired")

    async def delete_subject_data(self, subject: str) -> dict:
        """Right to erasure (Article 17): delete all data for a subject.
        Subject matches on session_id or key_prefix in audit/spend logs,
        and on subject/email in user_roles."""
        raise NotImplementedError(f"{type(self).__name__} does not implement delete_subject_data")

    async def export_subject_data(self, subject: str) -> dict:
        """DSAR (Article 15): export all data for a subject."""
        raise NotImplementedError(f"{type(self).__name__} does not implement export_subject_data")

    async def verify_audit_chain(self, anchor: dict | None = None) -> dict:
        """Verify the integrity of the audit log hash chain.
        Returns {"valid": bool, "total": int, "verified": int, "broken_at": int|None}.
        ``anchor`` ({"id", "hash"}, a head recorded outside the database) also checks
        the chain still contains that row."""
        raise NotImplementedError(f"{type(self).__name__} does not implement verify_audit_chain")

    async def get_audit_head(self) -> dict:
        """The newest audit row's id and hash plus the row count: the value to
        record outside the database. {"id": 0, "hash": "GENESIS", "count": 0} when empty."""
        raise NotImplementedError(f"{type(self).__name__} does not implement get_audit_head")

    # Roles belong here, in the same user_roles table that erasure and export read.
    # RBACManager used to keep them in a table of its own, in a file of its own,
    # so Article 15/17 requests looked at a table nothing wrote to.

    async def set_user_roles(self, subject: str, email: str | None, roles: list[str]) -> None:
        """Replace the roles recorded for ``subject`` (one user_roles row per role)."""
        raise NotImplementedError(f"{type(self).__name__} does not implement set_user_roles")

    async def get_user_roles(self, subject: str) -> list[str]:
        """The roles recorded for ``subject``; empty when none are."""
        raise NotImplementedError(f"{type(self).__name__} does not implement get_user_roles")

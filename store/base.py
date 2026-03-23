from abc import ABC, abstractmethod
from typing import List, Optional, Any, Dict, Protocol, runtime_checkable
from models import LLMEndpoint, EndpointStatus


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
    async def get_all(self) -> List[LLMEndpoint]:
        """Returns all registered endpoints."""
        pass

    @abstractmethod
    async def get_pool(self) -> List[LLMEndpoint]:
        """Returns only 'VERIFIED' and 'Live' endpoints."""
        pass

    @abstractmethod
    async def get_by_status(self, status: EndpointStatus) -> List[LLMEndpoint]:
        """Returns endpoints filtered by their status."""
        pass

    @abstractmethod
    async def update_status(self, endpoint_id: str, status: EndpointStatus, metadata: Optional[Dict] = None):
        """Updates the status and metadata of an endpoint."""
        pass

    @abstractmethod
    async def update_metrics(self, endpoint_id: str, latency_ms: float, success_rate: float):
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

    async def log_spend(self, **kwargs):
        """Record a spend entry. No-op without persistent storage."""
        pass

    async def log_audit(self, **kwargs):
        """Record an audit entry. No-op without persistent storage."""
        pass

    async def query_spend(self, **kwargs) -> list:
        """Query spend data. Returns empty without persistent storage."""
        return []

    async def get_spend_total(self, **kwargs) -> dict:
        """Get spend totals. Returns zeros without persistent storage."""
        return {"requests": 0, "total_usd": 0.0, "total_prompt_tokens": 0, "total_completion_tokens": 0}

    async def query_audit(self, **kwargs) -> dict:
        """Query audit log. Returns empty without persistent storage."""
        return {"total": 0, "items": []}

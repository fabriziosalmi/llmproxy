from abc import ABC, abstractmethod
from typing import List, Optional, Any, Dict
from models import LLMEndpoint, EndpointStatus

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

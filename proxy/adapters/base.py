from abc import ABC, abstractmethod
from typing import Dict, Any, AsyncGenerator

class BaseModelAdapter(ABC):
    """Abstract interface for model-specific communication."""

    @abstractmethod
    async def request(self, url: str, body: Dict[str, Any], headers: Dict[str, str], session: Any) -> Any:
        """Sends a non-streaming request."""
        pass

    @abstractmethod
    async def stream(self, url: str, body: Dict[str, Any], headers: Dict[str, str], session: Any) -> AsyncGenerator[bytes, None]:
        """Sends a streaming request."""
        pass

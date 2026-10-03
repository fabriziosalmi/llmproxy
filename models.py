from datetime import datetime
from enum import IntEnum
from typing import Any

from pydantic import BaseModel, HttpUrl, computed_field, model_validator


class EndpointStatus(IntEnum):
    FOUND = 0  # Found but not yet analyzed
    IGNORED = 1  # Scanned and found useless or unreachable
    DISCOVERED = 2  # Reachable but needs configuration/interface
    VERIFIED = 3  # Verified and usable in the pool


DEFAULT_PROVIDER = "openai"


class LLMEndpoint(BaseModel):
    """A registered upstream.

    The provider lives in exactly one place: ``metadata["provider"]``, the only
    copy either store persists (the endpoints table has no provider column).
    ``provider`` is read from there. It used to be a separate field defaulting to
    "openai" that no store wrote or loaded, so every endpoint reloaded as
    "openai" whatever its metadata said, and the forwarder, which picks its
    adapter from this attribute, sent Anthropic and Google endpoints through the
    OpenAI adapter.
    """

    id: str
    url: HttpUrl
    status: EndpointStatus
    metadata: dict[str, Any] = {}
    last_verified: datetime | None = None
    latency_ms: float | None = 0.0
    success_rate: float | None = 0.0

    @model_validator(mode="before")
    @classmethod
    def _fold_provider_into_metadata(cls, data: Any) -> Any:
        """Accept ``provider=`` / the legacy ``provider_type=`` and store it in metadata.

        Metadata that already names a provider wins, so a persisted row is never
        overridden by a constructor default.
        """
        if not isinstance(data, dict):
            return data
        metadata = dict(data.get("metadata") or {})
        if not metadata.get("provider"):
            explicit = data.get("provider") or data.get("provider_type")
            if explicit:
                metadata["provider"] = explicit
        return {**data, "metadata": metadata}

    @computed_field  # type: ignore[prop-decorator]
    @property
    def provider(self) -> str:
        """openai, anthropic, google, azure, ollama, groq, together, mistral, deepseek, openai-compatible."""
        return str(self.metadata.get("provider") or DEFAULT_PROVIDER)


class AgentState(BaseModel):
    agent_name: str
    last_run: datetime
    processed_count: int
    active_tasks: list[str]

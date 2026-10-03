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

#: Measured health lives in the endpoints table's latency_ms/success_rate
#: columns and nowhere else on the stored row. Older writers also copied these
#: into the metadata JSON, where the copy went stale the moment a column was
#: updated on its own.
ENDPOINT_STAT_KEYS = ("latency_ms", "success_rate")


def split_endpoint_stats(
    metadata: dict[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """(metadata without the stat keys, the stat values that were in it)."""
    clean = dict(metadata or {})
    stats = {k: clean.pop(k) for k in ENDPOINT_STAT_KEYS if k in clean}
    return clean, stats


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
    def _normalise_metadata(cls, data: Any) -> Any:
        """Keep each fact in one place: provider in metadata, health in the fields.

        * ``provider=`` / the legacy ``provider_type=`` are folded into
          metadata; metadata that already names a provider wins, so a persisted
          row is never overridden by a constructor default.
        * ``latency_ms`` / ``success_rate`` found in metadata (rows written by
          older versions) are moved out of it. The field wins when it has a
          value; the metadata copy only fills a missing one.
        """
        if not isinstance(data, dict):
            return data
        metadata, stats = split_endpoint_stats(data.get("metadata"))
        if not metadata.get("provider"):
            explicit = data.get("provider") or data.get("provider_type")
            if explicit:
                metadata["provider"] = explicit
        merged = {**data, "metadata": metadata}
        for key, value in stats.items():
            if merged.get(key) is None:
                merged[key] = value
        return merged

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

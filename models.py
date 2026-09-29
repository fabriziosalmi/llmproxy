from datetime import datetime
from enum import IntEnum
from typing import Any

from pydantic import BaseModel, HttpUrl


class EndpointStatus(IntEnum):
    FOUND = 0  # Found but not yet analyzed
    IGNORED = 1  # Scanned and found useless or unreachable
    DISCOVERED = 2  # Reachable but needs configuration/interface
    VERIFIED = 3  # Verified and usable in the pool


class LLMEndpoint(BaseModel):
    id: str
    url: HttpUrl
    status: EndpointStatus
    provider: str = "openai"  # openai, anthropic, google, azure, ollama, groq, together, mistral, deepseek, openai-compatible
    provider_type: str | None = None  # legacy alias for provider
    metadata: dict[str, Any] = {}
    last_verified: datetime | None = None
    latency_ms: float | None = 0.0
    success_rate: float | None = 0.0
    tags: list[str] = []


class AgentState(BaseModel):
    agent_name: str
    last_run: datetime
    processed_count: int
    active_tasks: list[str]

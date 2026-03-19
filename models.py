from pydantic import BaseModel, HttpUrl
from enum import IntEnum
from typing import List, Optional, Dict, Any
from datetime import datetime

class EndpointStatus(IntEnum):
    FOUND = 0      # Found but not yet analyzed
    IGNORED = 1    # Scanned and found useless or unreachable
    DISCOVERED = 2 # Reachable but needs configuration/interface
    VERIFIED = 3   # Verified and usable in the pool

class LLMEndpoint(BaseModel):
    id: str
    url: HttpUrl
    status: EndpointStatus
    provider_type: Optional[str] = None # e.g. "openai-compatible", "hf-space", "gradio"
    metadata: Dict[str, Any] = {}
    last_verified: Optional[datetime] = None
    latency_ms: Optional[float] = None
    success_rate: float = 0.0
    tags: List[str] = []

class AgentState(BaseModel):
    agent_name: str
    last_run: datetime
    processed_count: int
    active_tasks: List[str]

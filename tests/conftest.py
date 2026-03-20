"""
Shared fixtures for E2E integration tests.

Provides a fully wired RotatorAgent with:
  - In-memory store (no SQLite/aiosqlite dependency)
  - Mocked model adapter (no real upstream calls)
  - Auth disabled (for testing without API keys)
  - All plugins loaded from manifest
"""

import json
import asyncio
import pytest
from typing import List, Optional, Dict, Any
from unittest.mock import AsyncMock, MagicMock

from models import LLMEndpoint, EndpointStatus
from store.base import BaseRepository


class InMemoryRepository(BaseRepository):
    """Minimal in-memory store for E2E tests. No disk I/O."""

    def __init__(self):
        self._endpoints: Dict[str, LLMEndpoint] = {}
        self._state: Dict[str, Any] = {}

    async def init(self):
        pass

    async def add_endpoint(self, endpoint: LLMEndpoint):
        self._endpoints[endpoint.id] = endpoint

    async def remove_endpoint(self, endpoint_id: str):
        self._endpoints.pop(endpoint_id, None)

    async def get_all(self) -> List[LLMEndpoint]:
        return list(self._endpoints.values())

    def get_pool(self) -> List[LLMEndpoint]:
        return [e for e in self._endpoints.values() if e.status == EndpointStatus.VERIFIED]

    async def get_by_status(self, status: EndpointStatus) -> List[LLMEndpoint]:
        return [e for e in self._endpoints.values() if e.status == status]

    async def update_status(self, endpoint_id: str, status: EndpointStatus, metadata: Optional[Dict] = None):
        if endpoint_id in self._endpoints:
            self._endpoints[endpoint_id].status = status
            if metadata:
                self._endpoints[endpoint_id].metadata = metadata

    async def update_metrics(self, endpoint_id: str, latency_ms: float, success_rate: float):
        if endpoint_id in self._endpoints:
            self._endpoints[endpoint_id].latency_ms = latency_ms
            self._endpoints[endpoint_id].success_rate = success_rate

    async def set_state(self, key: str, value: Any):
        self._state[key] = value

    async def get_state(self, key: str, default: Any = None) -> Any:
        return self._state.get(key, default)


def make_openai_response(content: str = "Hello! How can I help?", model: str = "gpt-4"):
    """Build a realistic OpenAI chat completion response."""
    return {
        "id": "chatcmpl-test123",
        "object": "chat.completion",
        "model": model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": content},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
    }


def minimal_config(auth_enabled: bool = False):
    """Minimal config dict that satisfies RotatorAgent.__init__."""
    return {
        "server": {
            "port": 8090,
            "host": "0.0.0.0",
            "cors_origins": ["*"],
            "auth": {"enabled": auth_enabled, "api_keys_env": "LLM_PROXY_TEST_KEYS"},
            "metrics": {"enabled": False, "port": 9091},
            "admin": {"port": 8081},
            "vllm": {"model_path": None},
        },
        "caching": {"enabled": False},
        "budget": {"monthly_limit": 1000.0, "soft_limit": 800.0},
        "security": {"enabled": True},
        "plugins": {},
        "local_llm": {"host": "http://localhost:1234", "model": "test"},
    }

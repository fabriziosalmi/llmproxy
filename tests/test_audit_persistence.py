"""Audit-persistence tests — verify every entrypoint writes to the audit ledger.

Background: a live walkthrough on 2026-05-20 showed `/api/v1/analytics/spend`
reporting 12 completed requests while `/api/v1/audit/verify` returned
`{total: 0}` — the audit chain was empty despite real traffic. Root cause:
`/v1/completions` legacy never called `log_audit`, and the forwarder's
streaming finally block only persisted spend, not audit.

These tests pin the fix: each entrypoint MUST write one audit entry per
request, and the Prometheus counter must record outcome.
"""

import asyncio
import pytest
import pytest_asyncio
import httpx
from typing import Any, Dict, List
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from unittest.mock import AsyncMock, MagicMock

from conftest import InMemoryRepository, minimal_config, make_openai_response


class _AuditRecordingStore(InMemoryRepository):
    """Extends the in-memory repo with the audit/spend persistence surface
    that the production SQL store exposes."""

    def __init__(self):
        super().__init__()
        self.audit_entries: List[Dict[str, Any]] = []
        self.spend_entries: List[Dict[str, Any]] = []

    async def log_audit(self, **kwargs):
        self.audit_entries.append(kwargs)

    async def log_spend(self, **kwargs):
        self.spend_entries.append(kwargs)


@pytest.fixture
def store():
    return _AuditRecordingStore()


@pytest.fixture
def agent(store):
    """Lightweight agent wiring chat + completions routes only."""
    from proxy.routes import chat_router, completions_router

    class _A:
        pass

    a = _A()
    a.store = store
    a.config = minimal_config(auth_enabled=False)
    a.proxy_enabled = True
    a.priority_mode = False
    a.total_cost_today = 0.0
    a._budget_lock = asyncio.Lock()
    a._background_tasks = set()
    a.deduplicator = MagicMock()
    a.exporter = None
    a.webhooks = MagicMock()
    a.webhooks.dispatch = AsyncMock()
    a.identity = MagicMock()
    a.identity.enabled = False
    a.rbac = MagicMock()
    a.rbac.check_permission = MagicMock(return_value=True)
    a.rbac.check_quota = AsyncMock(return_value=True)
    a.rbac.set_user_roles = AsyncMock()
    a._verify_api_key = MagicMock(return_value=True)
    a._get_api_keys = MagicMock(return_value=[])
    a.enqueue_write = lambda *a, **k: None

    def _spawn_task(coro):
        t = asyncio.create_task(coro)
        a._background_tasks.add(t)
        t.add_done_callback(a._background_tasks.discard)
        return t

    a._spawn_task = _spawn_task

    async def flush_budget_now():
        pass

    a.flush_budget_now = flush_budget_now

    async def fake_proxy_request(request, body=None, session_id="default"):
        return JSONResponse(content=make_openai_response(model=body.get("model", "test")), status_code=200)

    a.proxy_request = fake_proxy_request

    app = FastAPI()
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
    app.include_router(chat_router(a))
    app.include_router(completions_router(a))
    a.app = app
    return a


@pytest_asyncio.fixture
async def client(agent):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=agent.app),
        base_url="http://test",
    ) as c:
        yield c


async def _wait_for_audit(store, timeout=1.0):
    """Audit writes are fired via _spawn_task — yield to the event loop."""
    deadline = asyncio.get_event_loop().time() + timeout
    while not store.audit_entries and asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_chat_completions_writes_audit_entry(client, store):
    resp = await client.post(
        "/v1/chat/completions",
        json={"model": "gpt-4", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 200
    await _wait_for_audit(store)
    assert len(store.audit_entries) == 1, f"Expected 1 audit entry, got {store.audit_entries}"
    e = store.audit_entries[0]
    assert e["model"] == "gpt-4"
    assert e["prompt_tokens"] == 10
    assert e["completion_tokens"] == 20
    assert e["status"] == 200


@pytest.mark.asyncio
async def test_legacy_completions_writes_audit_entry(client, store):
    """Regression test for live finding 2026-05-20:
    `/v1/completions` legacy was not writing to the audit ledger."""
    resp = await client.post(
        "/v1/completions",
        json={"model": "qwen3-coder", "prompt": "hello world"},
    )
    assert resp.status_code == 200
    await _wait_for_audit(store)
    assert len(store.audit_entries) == 1, f"Expected 1 audit entry, got {store.audit_entries}"
    e = store.audit_entries[0]
    assert e["model"] == "qwen3-coder"
    assert e["prompt_tokens"] == 10
    assert e["completion_tokens"] == 20


@pytest.mark.asyncio
async def test_legacy_completions_writes_spend_entry(client, store):
    """Parity check: spend log should also fire for legacy completions."""
    resp = await client.post(
        "/v1/completions",
        json={"model": "qwen3-coder", "prompt": "hello world"},
    )
    assert resp.status_code == 200
    await _wait_for_audit(store)
    assert len(store.spend_entries) == 1, f"Expected 1 spend entry, got {store.spend_entries}"
    assert store.spend_entries[0]["model"] == "qwen3-coder"


@pytest.mark.asyncio
async def test_audit_counter_increments(client, store):
    """The Prometheus counter must record at least one ok outcome after
    successful audit writes. Cleared and re-checked for isolation."""
    from core.metrics import AUDIT_PERSISTENCE

    # Snapshot counter value before
    def _val(route: str, outcome: str) -> float:
        try:
            return AUDIT_PERSISTENCE.labels(route=route, outcome=outcome)._value.get()
        except Exception:
            return 0.0

    before = _val("chat", "ok") + _val("completions", "ok")
    await client.post(
        "/v1/chat/completions",
        json={"model": "gpt-4", "messages": [{"role": "user", "content": "hi"}]},
    )
    await client.post(
        "/v1/completions",
        json={"model": "qwen3-coder", "prompt": "hi"},
    )
    await _wait_for_audit(store)
    # Give the background task a beat to finish
    await asyncio.sleep(0.05)
    after = _val("chat", "ok") + _val("completions", "ok")
    assert after - before >= 2, f"Counter did not increment as expected: before={before} after={after}"

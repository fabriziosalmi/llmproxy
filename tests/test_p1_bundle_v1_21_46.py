"""Regression tests for the v1.21.46 P1 bundle (draconian-audit findings).

Four fixes shipped together:

  1. PRE_FLIGHT block path raises HTTPException instead of returning None
     when a plugin issues `action=block` (proxy/request_pipeline.py).
     Pre-fix: budget_guard / loop_breaker blocks → handler returned None →
     FastAPI 500 instead of the plugin's intended 4xx.

  2. README test count updated 942 → 1183. (Doc fix; no test required —
     covered by the README content itself.)

  3. /api/v1/identity/exchange returns generic "Invalid token" instead of
     leaking the JWT validation reason (proxy/routes/identity.py).

  4. GDPR /api/v1/gdpr/erase/{subject} writes the audit row BEFORE the
     destructive delete so the trail exists regardless of delete outcome
     (proxy/routes/gdpr.py). Fail-closed if pre-audit can't be written.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from httpx import ASGITransport, AsyncClient


# ── Fix 1: PRE_FLIGHT block raises, doesn't return None ───────────


class _StubOrchestrator:
    """Just enough surface for process_proxy_request to run through
    PRE_FLIGHT and observe a block."""

    def __init__(self):
        self.config = {}
        self.plugin_state = MagicMock()
        self.negative_cache = MagicMock()
        self.negative_cache.check = MagicMock(return_value=None)
        self.security = MagicMock()
        self.security.inspect = AsyncMock(return_value=None)
        self.plugin_manager = MagicMock()
        self.webhooks = MagicMock()
        self.webhooks.dispatch = AsyncMock()
        self._budget_lock = asyncio.Lock()
        self._spawn_task = lambda coro: (
            asyncio.create_task(coro) if asyncio.iscoroutine(coro) else None
        )

    async def execute_pre_flight_block(self, hook, ctx):
        """Simulates a plugin in PRE_FLIGHT issuing action=block."""
        from core.plugin_engine import PluginHook

        if hook == PluginHook.PRE_FLIGHT:
            ctx.stop_chain = True
            ctx.error = "Daily budget exceeded"
            ctx.metadata["_block_status"] = 402  # Payment Required


@pytest.mark.asyncio
async def test_pre_flight_block_raises_http_exception():
    """The block path was returning ctx.response (None) instead of raising.
    A budget_guard block must surface as 402 Payment Required, not 500."""
    from proxy.request_pipeline import process_proxy_request
    from core.plugin_engine import PluginHook

    orch = _StubOrchestrator()

    async def execute_ring(hook, ctx):
        if hook == PluginHook.INGRESS:
            return  # pass through
        if hook == PluginHook.PRE_FLIGHT:
            ctx.stop_chain = True
            ctx.error = "Daily budget exceeded"
            ctx.metadata["_block_status"] = 402

    orch.plugin_manager.execute_ring = execute_ring

    request = MagicMock()
    request.client.host = "127.0.0.1"
    request.headers = {"cache-control": ""}

    with pytest.raises(HTTPException) as exc_info:
        await process_proxy_request(
            orch, request, body={"messages": []}, session_id="test"
        )
    assert exc_info.value.status_code == 402
    assert "budget" in exc_info.value.detail.lower()


@pytest.mark.asyncio
async def test_pre_flight_block_uses_default_403_when_no_status_set():
    """If a plugin sets stop_chain + error but forgets _block_status,
    the handler should default to 403 (Forbidden) — generic block."""
    from proxy.request_pipeline import process_proxy_request
    from core.plugin_engine import PluginHook

    orch = _StubOrchestrator()

    async def execute_ring(hook, ctx):
        if hook == PluginHook.PRE_FLIGHT:
            ctx.stop_chain = True
            ctx.error = "Loop detected"
            # Note: no _block_status set

    orch.plugin_manager.execute_ring = execute_ring

    request = MagicMock()
    request.client.host = "127.0.0.1"
    request.headers = {"cache-control": ""}

    with pytest.raises(HTTPException) as exc_info:
        await process_proxy_request(
            orch, request, body={"messages": []}, session_id="test"
        )
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_pre_flight_cache_hit_still_returns_response():
    """Cache-hit path must still work — it's a different stop_chain
    path that DOES set ctx.response. Don't break it."""
    from proxy.request_pipeline import process_proxy_request
    from core.plugin_engine import PluginHook
    from fastapi.responses import JSONResponse

    orch = _StubOrchestrator()
    cached_response = JSONResponse(
        content={"choices": [{"message": {"content": "cached"}}]}
    )

    async def execute_ring(hook, ctx):
        if hook == PluginHook.PRE_FLIGHT:
            ctx.stop_chain = True
            ctx.metadata["_cache_hit"] = True
            ctx.response = cached_response

    orch.plugin_manager.execute_ring = execute_ring

    request = MagicMock()
    request.client.host = "127.0.0.1"
    request.headers = {"cache-control": ""}

    response = await process_proxy_request(
        orch,
        request,
        body={"messages": [], "stream": False},
        session_id="test",
    )
    assert response is cached_response


# ── Fix 3: identity/exchange error message generic ────────────────


@pytest.mark.asyncio
async def test_identity_exchange_returns_generic_error_on_invalid_token(caplog):
    """Pre-fix: detail=str(e) leaked "Token expired" / "Invalid issuer".
    Post-fix: callers see "Invalid token"; the precise reason is logged
    server-side at WARNING level for ops."""
    import logging

    from proxy.routes.identity import create_router

    agent = MagicMock()
    agent.identity = MagicMock()
    agent.identity.enabled = True
    agent.identity.verify_token = AsyncMock(side_effect=ValueError("Token expired"))
    agent.config = {}

    app = FastAPI()
    app.include_router(create_router(agent))

    with caplog.at_level(logging.WARNING, logger="llmproxy.routes.identity"):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            resp = await c.post("/api/v1/identity/exchange", json={"token": "fake"})

    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid token"
    # Precise reason is in the server-side log, not the response body.
    assert any("Token expired" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_identity_exchange_unrecognized_provider_also_generic():
    """The unrecognized-provider path also returned a distinct message.
    Pre-fix: "Unrecognized JWT provider". Post-fix: same "Invalid token"
    so the two failure modes can't be distinguished by an attacker."""
    from proxy.routes.identity import create_router

    agent = MagicMock()
    agent.identity = MagicMock()
    agent.identity.enabled = True
    agent.identity.verify_token = AsyncMock(return_value=None)  # no provider matches
    agent.config = {}

    app = FastAPI()
    app.include_router(create_router(agent))

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        resp = await c.post("/api/v1/identity/exchange", json={"token": "fake"})

    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid token"


# ── Fix 4: GDPR audit-before-delete ───────────────────────────────


class _GDPRStubStore:
    """Minimal store with controllable audit/delete failure modes."""

    def __init__(self):
        self.audit_calls: list[dict] = []
        self.delete_calls: list[str] = []
        self._fail_audit = False
        self._fail_delete = False
        self._delete_returns = {
            "audit_deleted": 1,
            "spend_deleted": 1,
            "roles_deleted": 0,
        }

    async def log_audit(self, **kwargs):
        if self._fail_audit:
            raise RuntimeError("audit chain unavailable")
        self.audit_calls.append(kwargs)

    async def delete_subject_data(self, subject):
        if self._fail_delete:
            raise RuntimeError("delete failed")
        self.delete_calls.append(subject)
        return self._delete_returns


def _gdpr_app(store):
    """Build a tiny app exposing only the gdpr router for these tests."""
    from proxy.routes.gdpr import create_router as gdpr_router

    agent = MagicMock()
    agent.config = {"server": {"auth": {"enabled": False}}}
    agent.store = store
    app = FastAPI()
    app.add_middleware(
        CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
    )
    app.include_router(gdpr_router(agent))
    return app


@pytest.mark.asyncio
async def test_gdpr_erase_writes_audit_before_delete():
    """The audit row must be in the chain BEFORE delete_subject_data is
    invoked. Order is asserted by inspecting call sequencing."""
    store = _GDPRStubStore()
    # Track call order via a shared list.
    order: list[str] = []
    real_log = store.log_audit
    real_delete = store.delete_subject_data

    async def trace_log(**kw):
        order.append("audit")
        await real_log(**kw)

    async def trace_delete(subject):
        order.append("delete")
        return await real_delete(subject)

    store.log_audit = trace_log
    store.delete_subject_data = trace_delete

    app = _gdpr_app(store)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        resp = await c.post("/api/v1/gdpr/erase/longenough-user-id")

    assert resp.status_code == 200
    assert order == ["audit", "delete"]


@pytest.mark.asyncio
async def test_gdpr_erase_refuses_when_audit_fails():
    """Fail-closed: if the pre-audit can't be written, the destructive
    delete must NOT run. Pre-fix: delete ran first, audit failure left
    a silent gap."""
    store = _GDPRStubStore()
    store._fail_audit = True

    app = _gdpr_app(store)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        resp = await c.post("/api/v1/gdpr/erase/longenough-user-id")

    assert resp.status_code == 503
    assert "fail-closed" in resp.json()["detail"].lower()
    # Delete must not have been called.
    assert store.delete_calls == []


@pytest.mark.asyncio
async def test_gdpr_erase_audit_persists_when_delete_fails():
    """If delete fails AFTER pre-audit succeeded, the audit row stays.
    The operator can investigate from the trail; the caller gets 500."""
    store = _GDPRStubStore()
    store._fail_delete = True

    app = _gdpr_app(store)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        resp = await c.post("/api/v1/gdpr/erase/longenough-user-id")

    assert resp.status_code == 500
    # Audit row IS in the store — the trail is intact.
    assert len(store.audit_calls) == 1
    assert store.audit_calls[0]["session_id"] == "GDPR_SYSTEM"


@pytest.mark.asyncio
async def test_gdpr_erase_audit_intent_includes_phase_field():
    """The pre-audit row carries phase=intent so the trail makes clear
    this row was written BEFORE the deletion attempt."""
    import json

    store = _GDPRStubStore()
    app = _gdpr_app(store)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        resp = await c.post("/api/v1/gdpr/erase/longenough-user-id")
    assert resp.status_code == 200

    audit_meta = json.loads(store.audit_calls[0]["metadata"])
    assert audit_meta["phase"] == "intent"
    assert audit_meta["action"] == "erase"
    assert audit_meta["subject"] == "longenough-user-id"

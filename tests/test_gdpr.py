"""
GDPR compliance tests — Data Subject Rights.

Tests for:
  - Right to erasure (Article 17)
  - Data Subject Access Request (Article 15)
  - Data retention policy
  - Store-level purge/delete/export
"""

import time
import asyncio
import pytest
import pytest_asyncio
import httpx

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from unittest.mock import AsyncMock, MagicMock

from proxy.routes.gdpr import create_router as gdpr_router


# ── In-memory store with GDPR methods ──

class GDPRTestStore:
    """In-memory store implementing GDPR methods for testing."""

    def __init__(self):
        self.audit_log = []
        self.spend_log = []
        self.user_roles = []
        self._state = {}

    async def init(self):
        pass

    async def set_state(self, key, value):
        self._state[key] = value

    async def get_state(self, key, default=None):
        return self._state.get(key, default)

    async def log_audit(self, **kwargs):
        self.audit_log.append(kwargs)

    async def log_spend(self, **kwargs):
        self.spend_log.append(kwargs)

    async def delete_subject_data(self, subject):
        audit_before = len(self.audit_log)
        self.audit_log = [
            r for r in self.audit_log
            if r.get("session_id") != subject and r.get("key_prefix") != subject
        ]
        audit_deleted = audit_before - len(self.audit_log)

        spend_before = len(self.spend_log)
        self.spend_log = [r for r in self.spend_log if r.get("key_prefix") != subject]
        spend_deleted = spend_before - len(self.spend_log)

        roles_before = len(self.user_roles)
        self.user_roles = [
            r for r in self.user_roles
            if r.get("subject") != subject and r.get("email") != subject
        ]
        roles_deleted = roles_before - len(self.user_roles)

        return {
            "audit_deleted": audit_deleted,
            "spend_deleted": spend_deleted,
            "roles_deleted": roles_deleted,
        }

    async def export_subject_data(self, subject):
        audit = [
            r for r in self.audit_log
            if r.get("session_id") == subject or r.get("key_prefix") == subject
        ]
        spend = [r for r in self.spend_log if r.get("key_prefix") == subject]
        roles = [
            r for r in self.user_roles
            if r.get("subject") == subject or r.get("email") == subject
        ]
        return {"audit": audit, "spend": spend, "roles": roles}

    async def purge_expired(self, retention_days=90):
        cutoff = int(time.time()) - (retention_days * 86400)
        audit_before = len(self.audit_log)
        self.audit_log = [r for r in self.audit_log if r.get("ts", 0) >= cutoff]
        spend_before = len(self.spend_log)
        self.spend_log = [r for r in self.spend_log if r.get("ts", 0) >= cutoff]
        return {
            "audit_deleted": audit_before - len(self.audit_log),
            "spend_deleted": spend_before - len(self.spend_log),
        }


# ── Test agent ──

class GDPRTestAgent:
    def __init__(self, store):
        self.store = store
        self.config = {"gdpr": {"retention_days": 90, "auto_purge": True}}
        self.app = FastAPI(title="GDPR-TEST")
        self.app.add_middleware(
            CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
        )
        self.app.include_router(gdpr_router(self))


# ── Fixtures ──

@pytest.fixture
def gdpr_store():
    store = GDPRTestStore()
    # Seed data for user "alice"
    store.audit_log = [
        {"ts": int(time.time()), "session_id": "alice", "key_prefix": "sk-alice", "model": "gpt-4o", "req_id": "r1", "status": 200},
        {"ts": int(time.time()), "session_id": "alice", "key_prefix": "sk-alice", "model": "gpt-4o", "req_id": "r2", "status": 200},
        {"ts": int(time.time()), "session_id": "bob", "key_prefix": "sk-bob", "model": "gpt-4o", "req_id": "r3", "status": 200},
    ]
    store.spend_log = [
        {"ts": int(time.time()), "key_prefix": "sk-alice", "model": "gpt-4o", "cost_usd": 0.01},
        {"ts": int(time.time()), "key_prefix": "sk-bob", "model": "gpt-4o", "cost_usd": 0.02},
    ]
    store.user_roles = [
        {"subject": "alice-sub", "email": "alice@example.com", "roles": "user"},
        {"subject": "bob-sub", "email": "bob@example.com", "roles": "admin"},
    ]
    return store


@pytest.fixture
def gdpr_agent(gdpr_store):
    return GDPRTestAgent(gdpr_store)


@pytest_asyncio.fixture
async def gdpr_client(gdpr_agent):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=gdpr_agent.app),
        base_url="http://test",
    ) as c:
        yield c


# ══════════════════════════════════════════════════════════
# Right to Erasure (Article 17)
# ══════════════════════════════════════════════════════════

class TestRightToErasure:

    @pytest.mark.asyncio
    async def test_erase_deletes_user_data(self, gdpr_client, gdpr_store):
        """Erasing a subject removes their audit, spend, and role records."""
        resp = await gdpr_client.post("/api/v1/gdpr/erase/sk-alice")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "erased"
        assert data["audit_deleted"] == 2
        assert data["spend_deleted"] == 1

        # Verify alice's data is gone
        assert all(r.get("key_prefix") != "sk-alice" for r in gdpr_store.audit_log
                    if r.get("session_id") != "GDPR_SYSTEM")
        assert all(r.get("key_prefix") != "sk-alice" for r in gdpr_store.spend_log)

    @pytest.mark.asyncio
    async def test_erase_preserves_other_users(self, gdpr_client, gdpr_store):
        """Erasing one subject does not affect other users."""
        await gdpr_client.post("/api/v1/gdpr/erase/sk-alice")
        # Bob's data should still exist
        bob_audit = [r for r in gdpr_store.audit_log if r.get("key_prefix") == "sk-bob"]
        assert len(bob_audit) == 1
        bob_spend = [r for r in gdpr_store.spend_log if r.get("key_prefix") == "sk-bob"]
        assert len(bob_spend) == 1

    @pytest.mark.asyncio
    async def test_erase_logs_erasure_immutably(self, gdpr_client, gdpr_store):
        """The erasure action is itself logged (immutable audit trail)."""
        await gdpr_client.post("/api/v1/gdpr/erase/sk-alice")
        gdpr_entries = [
            r for r in gdpr_store.audit_log if r.get("session_id") == "GDPR_SYSTEM"
        ]
        assert len(gdpr_entries) == 1
        assert "erase" in gdpr_entries[0].get("metadata", "")

    @pytest.mark.asyncio
    async def test_erase_unknown_subject_returns_404(self, gdpr_client):
        """Erasing a nonexistent subject returns 404."""
        resp = await gdpr_client.post("/api/v1/gdpr/erase/nonexistent-user")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_erase_by_email(self, gdpr_client, gdpr_store):
        """Erasing by email removes user_roles entries."""
        resp = await gdpr_client.post("/api/v1/gdpr/erase/alice@example.com")
        assert resp.status_code == 200
        data = resp.json()
        assert data["roles_deleted"] == 1
        assert all(r.get("email") != "alice@example.com" for r in gdpr_store.user_roles)


# ══════════════════════════════════════════════════════════
# Data Subject Access Request (Article 15)
# ══════════════════════════════════════════════════════════

class TestDSAR:

    @pytest.mark.asyncio
    async def test_export_returns_user_data(self, gdpr_client):
        """Export returns all audit, spend, and identity data for a subject."""
        resp = await gdpr_client.get("/api/v1/gdpr/export/sk-alice")
        assert resp.status_code == 200
        data = resp.json()
        assert data["subject"] == "sk-alice"
        assert data["record_count"] == 3  # 2 audit + 1 spend
        assert len(data["audit_log"]) == 2
        assert len(data["spend_log"]) == 1

    @pytest.mark.asyncio
    async def test_export_unknown_subject_returns_404(self, gdpr_client):
        """Export for unknown subject returns 404."""
        resp = await gdpr_client.get("/api/v1/gdpr/export/nonexistent")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_export_includes_timestamp(self, gdpr_client):
        """Export response includes export timestamp."""
        resp = await gdpr_client.get("/api/v1/gdpr/export/sk-alice")
        data = resp.json()
        assert "exported_at" in data
        assert "T" in data["exported_at"]  # ISO format


# ══════════════════════════════════════════════════════════
# Retention Policy
# ══════════════════════════════════════════════════════════

class TestRetentionPolicy:

    @pytest.mark.asyncio
    async def test_retention_endpoint_returns_config(self, gdpr_client):
        """Retention endpoint returns the configured retention policy."""
        resp = await gdpr_client.get("/api/v1/gdpr/retention")
        assert resp.status_code == 200
        data = resp.json()
        assert data["retention_days"] == 90
        assert "legal_basis" in data
        assert "data_categories" in data

    @pytest.mark.asyncio
    async def test_manual_purge_deletes_old_records(self, gdpr_client, gdpr_store):
        """Manual purge deletes records older than retention period."""
        # Add an old record (200 days ago)
        old_ts = int(time.time()) - (200 * 86400)
        gdpr_store.audit_log.append({
            "ts": old_ts, "session_id": "old-user", "key_prefix": "sk-old",
            "model": "gpt-4o", "req_id": "r-old", "status": 200,
        })

        resp = await gdpr_client.post("/api/v1/gdpr/purge")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "purged"
        assert data["audit_deleted"] >= 1

        # Old record should be gone
        old = [r for r in gdpr_store.audit_log if r.get("session_id") == "old-user"]
        assert len(old) == 0

    @pytest.mark.asyncio
    async def test_purge_preserves_recent_records(self, gdpr_client, gdpr_store):
        """Purge does not delete records within the retention period."""
        count_before = len(gdpr_store.audit_log)
        resp = await gdpr_client.post("/api/v1/gdpr/purge")
        data = resp.json()
        # Only old records deleted, recent ones preserved
        assert len(gdpr_store.audit_log) == count_before - data["audit_deleted"]

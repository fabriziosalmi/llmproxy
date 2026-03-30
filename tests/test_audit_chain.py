"""
Tests for immutable audit ledger (hash chain).

Verifies that:
  - Each audit entry's hash includes the previous entry's hash
  - Tampered entries are detected by verify_audit_chain()
  - Deleted entries break the chain
  - Empty audit log verifies successfully
  - Legacy entries (no hashes) are skipped gracefully
"""

import time
import os
import tempfile
import pytest
import pytest_asyncio
import aiosqlite

# Import the real SQLStore to test hash chain logic
from store.sql_store import SQLiteStore as SQLStore


@pytest_asyncio.fixture
async def db_store():
    """Create a temporary SQLite store for testing."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    store = SQLStore(db_path=path)
    await store.init_db()
    yield store
    os.unlink(path)


def _make_audit_entry(req_id="r1", session_id="sess", key_prefix="sk-test",
                      model="gpt-4o", ts=None):
    return dict(
        ts=ts or int(time.time()),
        req_id=req_id,
        session_id=session_id,
        key_prefix=key_prefix,
        model=model,
        provider="openai",
        status=200,
        prompt_tokens=10,
        completion_tokens=5,
        cost_usd=0.01,
        latency_ms=100.0,
    )


# ══════════════════════════════════════════════════════════
# Hash Chain Integrity
# ══════════════════════════════════════════════════════════

class TestAuditHashChain:

    @pytest.mark.asyncio
    async def test_empty_log_verifies(self, db_store):
        """Empty audit log is valid."""
        result = await db_store.verify_audit_chain()
        assert result["valid"] is True
        assert result["total"] == 0

    @pytest.mark.asyncio
    async def test_single_entry_verifies(self, db_store):
        """A single entry chain is valid."""
        await db_store.log_audit(**_make_audit_entry())
        result = await db_store.verify_audit_chain()
        assert result["valid"] is True
        assert result["verified"] == 1

    @pytest.mark.asyncio
    async def test_multiple_entries_verify(self, db_store):
        """A chain of entries all verify correctly."""
        for i in range(5):
            await db_store.log_audit(**_make_audit_entry(req_id=f"r{i}"))

        result = await db_store.verify_audit_chain()
        assert result["valid"] is True
        assert result["verified"] == 5

    @pytest.mark.asyncio
    async def test_first_entry_has_genesis_prev(self, db_store):
        """First entry's prev_hash is 'GENESIS'."""
        await db_store.log_audit(**_make_audit_entry())

        async with aiosqlite.connect(db_store.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute("SELECT * FROM audit_log LIMIT 1") as cursor:
                row = dict(await cursor.fetchone())

        assert row["prev_hash"] == "GENESIS"
        assert len(row["entry_hash"]) == 64  # SHA256 hex

    @pytest.mark.asyncio
    async def test_entries_chain_to_previous(self, db_store):
        """Each entry's prev_hash matches the previous entry's entry_hash."""
        for i in range(3):
            await db_store.log_audit(**_make_audit_entry(req_id=f"r{i}"))

        async with aiosqlite.connect(db_store.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute("SELECT * FROM audit_log ORDER BY id ASC") as cursor:
                rows = [dict(r) for r in await cursor.fetchall()]

        assert rows[0]["prev_hash"] == "GENESIS"
        assert rows[1]["prev_hash"] == rows[0]["entry_hash"]
        assert rows[2]["prev_hash"] == rows[1]["entry_hash"]

    @pytest.mark.asyncio
    async def test_tampered_entry_detected(self, db_store):
        """Modifying an entry's data breaks the chain."""
        for i in range(3):
            await db_store.log_audit(**_make_audit_entry(req_id=f"r{i}"))

        # Tamper: change model of entry 2
        async with aiosqlite.connect(db_store.db_path) as conn:
            await conn.execute("UPDATE audit_log SET model = 'HACKED' WHERE id = 2")
            await conn.commit()

        result = await db_store.verify_audit_chain()
        assert result["valid"] is False
        assert result["broken_at"] == 2
        assert "tamper" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_deleted_entry_detected(self, db_store):
        """Deleting an entry breaks the chain."""
        for i in range(3):
            await db_store.log_audit(**_make_audit_entry(req_id=f"r{i}"))

        # Delete middle entry
        async with aiosqlite.connect(db_store.db_path) as conn:
            await conn.execute("DELETE FROM audit_log WHERE id = 2")
            await conn.commit()

        result = await db_store.verify_audit_chain()
        assert result["valid"] is False
        assert result["broken_at"] == 3  # Entry 3's prev_hash won't match

    @pytest.mark.asyncio
    async def test_tampered_hash_detected(self, db_store):
        """Modifying just the entry_hash is detected."""
        for i in range(2):
            await db_store.log_audit(**_make_audit_entry(req_id=f"r{i}"))

        async with aiosqlite.connect(db_store.db_path) as conn:
            await conn.execute("UPDATE audit_log SET entry_hash = 'fakehash' WHERE id = 1")
            await conn.commit()

        result = await db_store.verify_audit_chain()
        assert result["valid"] is False

    @pytest.mark.asyncio
    async def test_verify_reports_count(self, db_store):
        """Verify returns total and verified counts."""
        for i in range(10):
            await db_store.log_audit(**_make_audit_entry(req_id=f"r{i}"))

        result = await db_store.verify_audit_chain()
        assert result["total"] == 10
        assert result["verified"] == 10

"""
Coverage tests for store layer.

Targets: store/factory.py, store/sql_store.py (partial).
"""

import os
import pytest


# ── StorageFactory ────────────────────────────────────────────

class TestStorageFactory:

    def test_factory_returns_repository(self):
        from store.factory import StorageFactory
        repo = StorageFactory.get_repository("config.yaml")
        assert repo is not None

    def test_factory_accepts_custom_path(self):
        from store.factory import StorageFactory
        repo = StorageFactory.get_repository("config.minimal.yaml")
        assert repo is not None


# ── SQLiteStore ──────────────────────────────────────────

class TestSQLiteStore:

    @pytest.mark.asyncio
    async def test_init_creates_tables(self, tmp_path):
        from store.sql_store import SQLiteStore
        db_path = str(tmp_path / "test.db")
        repo = SQLiteStore(db_path=db_path)
        await repo.init_db()
        # Should not raise
        assert os.path.exists(db_path)

    @pytest.mark.asyncio
    async def test_state_set_and_get(self, tmp_path):
        from store.sql_store import SQLiteStore
        db_path = str(tmp_path / "test.db")
        repo = SQLiteStore(db_path=db_path)
        await repo.init_db()

        await repo.set_state("test_key", "test_value")
        result = await repo.get_state("test_key")
        assert result == "test_value"

    @pytest.mark.asyncio
    async def test_state_get_default(self, tmp_path):
        from store.sql_store import SQLiteStore
        db_path = str(tmp_path / "test.db")
        repo = SQLiteStore(db_path=db_path)
        await repo.init_db()

        result = await repo.get_state("nonexistent", "default_val")
        assert result == "default_val"

    @pytest.mark.asyncio
    async def test_state_overwrite(self, tmp_path):
        from store.sql_store import SQLiteStore
        db_path = str(tmp_path / "test.db")
        repo = SQLiteStore(db_path=db_path)
        await repo.init_db()

        await repo.set_state("key", "val1")
        await repo.set_state("key", "val2")
        result = await repo.get_state("key")
        assert result == "val2"

    @pytest.mark.asyncio
    async def test_state_complex_value(self, tmp_path):
        from store.sql_store import SQLiteStore
        db_path = str(tmp_path / "test.db")
        repo = SQLiteStore(db_path=db_path)
        await repo.init_db()

        await repo.set_state("complex", {"nested": [1, 2, 3], "flag": True})
        result = await repo.get_state("complex")
        assert result == {"nested": [1, 2, 3], "flag": True}

    @pytest.mark.asyncio
    async def test_endpoint_crud(self, tmp_path):
        from store.sql_store import SQLiteStore
        from models import LLMEndpoint, EndpointStatus
        db_path = str(tmp_path / "test.db")
        repo = SQLiteStore(db_path=db_path)
        await repo.init_db()

        ep = LLMEndpoint(
            id="test-ep",
            url="https://api.openai.com/v1",
            provider="openai",
            model="gpt-4o",
            status=EndpointStatus.VERIFIED,
        )
        await repo.add_endpoint(ep)

        all_eps = await repo.get_all()
        assert len(all_eps) == 1
        assert all_eps[0].id == "test-ep"

        pool = await repo.get_pool()
        assert len(pool) == 1

        await repo.remove_endpoint("test-ep")
        all_eps = await repo.get_all()
        assert len(all_eps) == 0

    @pytest.mark.asyncio
    async def test_update_status(self, tmp_path):
        from store.sql_store import SQLiteStore
        from models import LLMEndpoint, EndpointStatus
        db_path = str(tmp_path / "test.db")
        repo = SQLiteStore(db_path=db_path)
        await repo.init_db()

        ep = LLMEndpoint(
            id="ep1", url="https://api.test.com",
            provider="openai", model="gpt-4o",
            status=EndpointStatus.FOUND,
        )
        await repo.add_endpoint(ep)
        await repo.update_status("ep1", EndpointStatus.VERIFIED)

        pool = await repo.get_pool()
        assert len(pool) == 1
        assert pool[0].status == EndpointStatus.VERIFIED

    @pytest.mark.asyncio
    async def test_get_by_status(self, tmp_path):
        from store.sql_store import SQLiteStore
        from models import LLMEndpoint, EndpointStatus
        db_path = str(tmp_path / "test.db")
        repo = SQLiteStore(db_path=db_path)
        await repo.init_db()

        for i, status in enumerate([EndpointStatus.VERIFIED, EndpointStatus.FOUND, EndpointStatus.VERIFIED]):
            ep = LLMEndpoint(
                id=f"ep{i}", url=f"https://api{i}.test.com",
                provider="openai", model="gpt-4o", status=status,
            )
            await repo.add_endpoint(ep)

        verified = await repo.get_by_status(EndpointStatus.VERIFIED)
        assert len(verified) == 2

        pending = await repo.get_by_status(EndpointStatus.FOUND)
        assert len(pending) == 1

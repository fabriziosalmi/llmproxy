"""Roles are recorded where GDPR export and erasure look for them.

RBACManager kept roles in a ``user_roles`` table of its own, with a different
shape from the store's, in ``endpoints.db`` relative to the working directory
(the store uses ``data/endpoints.db``). The proxy recorded a user's roles on
every identity-authenticated request, and Article 15/17 requests read a table
nothing wrote to: the export returned no roles and erasure reported zero deleted
while the real row survived. It was also outside the data volume, so a restore
could not bring it back.
"""

import os
import sqlite3
import time

import pytest

from core.rbac import RBACManager
from store.sql_store import SQLiteStore

TEST_POSTGRES_DSN = os.environ.get("TEST_POSTGRES_DSN", "")


@pytest.fixture(params=["sqlite", "postgres"])
async def stack(request, tmp_path):
    """The roles path against both backends (Postgres when TEST_POSTGRES_DSN is set)."""
    if request.param == "sqlite":
        store = SQLiteStore(str(tmp_path / "data" / "endpoints.db"))
        await store.init_db()
    else:
        if not TEST_POSTGRES_DSN:
            pytest.skip("TEST_POSTGRES_DSN not set")
        asyncpg = pytest.importorskip("asyncpg")
        from store.pg_store import PostgresStore

        raw = await asyncpg.connect(TEST_POSTGRES_DSN)
        try:
            await raw.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        finally:
            await raw.close()
        store = PostgresStore(TEST_POSTGRES_DSN)
        await store.init_db()
    manager = RBACManager(str(tmp_path / "quotas.db"), store=store)
    yield store, manager, tmp_path
    await manager.close()
    await store.close()


async def test_export_returns_the_roles_the_proxy_recorded(stack):
    store, manager, _ = stack
    await manager.set_user_roles("sub-1", "ada@example.com", ["admin", "operator"])

    exported = await store.export_subject_data("sub-1")

    assert sorted(r["role"] for r in exported["roles"]) == ["admin", "operator"]
    assert {r["email"] for r in exported["roles"]} == {"ada@example.com"}


async def test_export_also_finds_them_by_email(stack):
    store, manager, _ = stack
    await manager.set_user_roles("sub-1", "ada@example.com", ["viewer"])

    exported = await store.export_subject_data("ada@example.com")

    assert [r["role"] for r in exported["roles"]] == ["viewer"]


async def test_erasure_deletes_the_roles_and_says_so(stack):
    store, manager, _ = stack
    await manager.set_user_roles("sub-1", "ada@example.com", ["admin", "operator"])

    result = await store.delete_subject_data("sub-1")

    assert result["roles_deleted"] == 2
    assert await store.get_user_roles("sub-1") == []
    assert await manager.get_user_roles("sub-1") == ["user"]  # the default


async def test_changed_roles_replace_the_old_ones(stack):
    store, manager, _ = stack
    await manager.set_user_roles("sub-1", "ada@example.com", ["admin", "operator"])
    await manager.set_user_roles("sub-1", "ada@example.com", ["viewer"])

    assert await store.get_user_roles("sub-1") == ["viewer"]


async def test_the_manager_creates_no_user_roles_table_of_its_own(stack):
    _, manager, tmp_path = stack
    await manager.set_user_roles("sub-1", None, ["user"])

    conn = sqlite3.connect(tmp_path / "quotas.db")
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master")}
    finally:
        conn.close()
    assert "user_roles" not in tables


async def test_unchanged_roles_are_not_rewritten_on_every_request(stack, monkeypatch):
    store, manager, _ = stack
    writes = []
    original = store.set_user_roles

    async def counting(subject, email, roles):
        writes.append(subject)
        await original(subject, email, roles)

    monkeypatch.setattr(store, "set_user_roles", counting)

    for _ in range(50):
        await manager.set_user_roles("sub-1", "ada@example.com", ["user"])
    assert writes == ["sub-1"]

    await manager.set_user_roles("sub-1", "ada@example.com", ["admin"])
    assert writes == ["sub-1", "sub-1"]


async def test_an_erased_subject_is_recorded_again_after_the_refresh_window(
    stack, monkeypatch
):
    store, manager, _ = stack
    await manager.set_user_roles("sub-1", "ada@example.com", ["user"])
    await store.delete_subject_data("sub-1")

    await manager.set_user_roles("sub-1", "ada@example.com", ["user"])
    assert await store.get_user_roles("sub-1") == []  # inside the window

    later = time.monotonic() + RBACManager.ROLE_REFRESH_SECONDS + 1
    monkeypatch.setattr(time, "monotonic", lambda: later)
    await manager.set_user_roles("sub-1", "ada@example.com", ["user"])

    assert await store.get_user_roles("sub-1") == ["user"]


async def test_a_manager_without_a_store_says_so(tmp_path):
    manager = RBACManager(str(tmp_path / "q.db"))
    try:
        with pytest.raises(RuntimeError, match="store"):
            await manager.set_user_roles("sub-1", None, ["user"])
    finally:
        await manager.close()

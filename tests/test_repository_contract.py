"""A repository that stores audit data implements the compliance operations.

SQLiteRepository, the backend StorageFactory builds by default, did not define
purge_expired, delete_subject_data, export_subject_data or verify_audit_chain,
so it inherited BaseRepository's stubs. On the default backend the retention
purge deleted nothing, erasure answered "no data found" and erased nothing, the
access request exported nothing, and /api/v1/audit/verify said "valid" for a
chain that had been tampered with. Every test of those features ran against the
inner store, a mock, or PostgresRepository, so none noticed.

These run the operations through the repository classes themselves.
"""

import inspect
import os
import time

import pytest

from store.base import BaseRepository
from store.pg_store import PostgresRepository
from store.store import SQLiteRepository

TEST_POSTGRES_DSN = os.environ.get("TEST_POSTGRES_DSN", "")
DAY = 86400

COMPLIANCE_OPERATIONS = (
    "purge_expired",
    "delete_subject_data",
    "export_subject_data",
    "verify_audit_chain",
    "get_audit_head",
)


@pytest.mark.parametrize("repository", [SQLiteRepository, PostgresRepository])
def test_a_concrete_repository_does_not_leave_the_compliance_operations_to_the_base(
    repository,
):
    inherited = [name for name in COMPLIANCE_OPERATIONS if name not in vars(repository)]
    assert not inherited, f"{repository.__name__} inherits the base class's version of {inherited}"


def test_every_async_operation_of_the_base_is_overridden_by_each_real_repository():
    operations = [
        n for n, v in vars(BaseRepository).items()
        if inspect.iscoroutinefunction(v) and not n.startswith("_")
    ]
    for repository in (SQLiteRepository, PostgresRepository):
        missing = [n for n in operations if n not in vars(repository)]
        assert not missing, f"{repository.__name__} inherits stubs for {missing}"


@pytest.mark.parametrize("operation,args", [
    ("purge_expired", (90,)),
    ("delete_subject_data", ("subject-1234",)),
    ("export_subject_data", ("subject-1234",)),
    ("verify_audit_chain", ()),
    ("get_audit_head", ()),
])
async def test_the_base_class_refuses_instead_of_pretending(operation, args):
    # BaseRepository is abstract, so call the unbound default directly.
    with pytest.raises(NotImplementedError, match=operation):
        await getattr(BaseRepository, operation)(None, *args)


@pytest.fixture(params=["sqlite", "postgres"])
async def repo(request, tmp_path):
    if request.param == "sqlite":
        r = SQLiteRepository(str(tmp_path / "repo.db"))
        await r.init()
        yield r
        await r.sql.close()
        return
    if not TEST_POSTGRES_DSN:
        pytest.skip("TEST_POSTGRES_DSN not set")
    asyncpg = pytest.importorskip("asyncpg")
    raw = await asyncpg.connect(TEST_POSTGRES_DSN)
    try:
        await raw.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    finally:
        await raw.close()
    r = PostgresRepository(TEST_POSTGRES_DSN)
    await r.init()
    yield r
    await r.sql.close()


async def _log(repo, age_days, req_id, session="alice-session-1"):
    await repo.log_audit(
        ts=int(time.time()) - age_days * DAY, req_id=req_id, session_id=session,
        key_prefix="sk-test", model="m", provider="p", status=200, prompt_tokens=1,
        completion_tokens=1, cost_usd=0.0, latency_ms=1.0,
    )


async def _audit_total(repo):
    return (await repo.query_audit(limit=1000))["total"]


async def test_the_retention_purge_deletes_through_the_repository(repo):
    for i, age in enumerate([200, 150, 10, 5]):
        await _log(repo, age, f"r{i}")
    assert await _audit_total(repo) == 4

    result = await repo.purge_expired(90)

    assert result["audit_deleted"] == 2
    assert await _audit_total(repo) == 3  # two survivors and the removal record
    assert (await repo.verify_audit_chain())["valid"] is True


async def test_erasure_erases_through_the_repository(repo):
    await _log(repo, 3, "a1", session="alice-session-1")
    await _log(repo, 2, "b1", session="bob-session-22")
    await _log(repo, 1, "a2", session="alice-session-1")

    result = await repo.delete_subject_data("alice-session-1")

    assert result["audit_deleted"] == 2
    assert await _audit_total(repo) == 2  # bob's row and the removal record
    assert (await repo.verify_audit_chain())["valid"] is True


async def test_the_access_request_exports_through_the_repository(repo):
    await _log(repo, 3, "a1", session="alice-session-1")
    await _log(repo, 2, "b1", session="bob-session-22")

    exported = await repo.export_subject_data("alice-session-1")

    assert [row["req_id"] for row in exported["audit"]] == ["a1"]


async def test_verification_through_the_repository_detects_tampering(repo):
    for i in range(4):
        await _log(repo, 4 - i, f"r{i}")
    healthy = await repo.verify_audit_chain()
    assert healthy["valid"] is True and healthy["verified"] == 4

    inner = repo.sql
    if hasattr(inner, "_get_conn"):
        conn = await inner._get_conn()
        await conn.execute("UPDATE audit_log SET cost_usd = 99 WHERE req_id = 'r2'")
        await conn.commit()
    else:
        pool = await inner.init_pool()
        await pool.execute("UPDATE audit_log SET cost_usd = 99 WHERE req_id = 'r2'")

    verdict = await repo.verify_audit_chain()

    assert verdict["valid"] is False, "the repository reported a tampered chain as valid"
    assert "entry_hash mismatch" in verdict["error"]

"""The verified pool is a snapshot that writes invalidate.

select_endpoint asked the store for the pool on every request: a SELECT,
json.loads per row and a pydantic model per row. The pool only changes through
add_endpoint, remove_endpoint, update_status and update_metrics, so the stores
keep a snapshot and drop it inside those writes. A short TTL is the backstop
for a writer the store cannot see.
"""

import os

import pytest

from models import EndpointStatus, LLMEndpoint
from store.pool_cache import PoolCache
from store.sql_store import SQLiteStore

TEST_POSTGRES_DSN = os.environ.get("TEST_POSTGRES_DSN", "")


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


# ── PoolCache ───────────────────────────────────────────────────────────────


def test_a_stored_snapshot_is_served_until_it_expires():
    clock = Clock()
    cache = PoolCache(ttl_s=5, clock=clock)
    cache.put(["a"], cache.generation)

    assert cache.get() == ["a"]
    clock.now += 4.9
    assert cache.get() == ["a"]
    clock.now += 0.2
    assert cache.get() is None


def test_get_returns_a_copy_so_callers_cannot_reorder_the_snapshot():
    cache = PoolCache()
    cache.put(["a", "b"], cache.generation)

    first = cache.get()
    first.reverse()

    assert cache.get() == ["a", "b"]


def test_invalidate_drops_the_snapshot():
    cache = PoolCache()
    cache.put(["a"], cache.generation)
    cache.invalidate()
    assert cache.get() is None


def test_a_read_in_flight_during_a_write_cannot_store_its_stale_result():
    cache = PoolCache()
    generation = cache.generation  # reader starts, reads the old rows...
    cache.invalidate()  # ...a write lands and invalidates...
    cache.put(["old"], generation)  # ...the reader finishes and tries to store.

    assert cache.get() is None


def test_an_empty_pool_is_a_valid_snapshot():
    cache = PoolCache()
    cache.put([], cache.generation)
    assert cache.get() == []


# ── the stores ──────────────────────────────────────────────────────────────


@pytest.fixture(params=["sqlite", "postgres"])
async def store(request, tmp_path):
    if request.param == "sqlite":
        s = SQLiteStore(str(tmp_path / "pool.db"))
        await s.init_db()
        yield s
        await s.close()
        return
    if not TEST_POSTGRES_DSN:
        pytest.skip("TEST_POSTGRES_DSN not set")
    asyncpg = pytest.importorskip("asyncpg")
    from store.pg_store import PostgresStore

    raw = await asyncpg.connect(TEST_POSTGRES_DSN)
    try:
        await raw.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    finally:
        await raw.close()
    s = PostgresStore(TEST_POSTGRES_DSN)
    await s.init_db()
    yield s
    await s.close()


def _endpoint(id_, status=EndpointStatus.VERIFIED):
    return LLMEndpoint(
        id=id_, url=f"http://{id_}.test/v1", status=status, metadata={"provider": "openai"}
    )


def _count_reads(store, monkeypatch):
    reads = {"n": 0}
    original = store.get_by_status

    async def counting(status):
        reads["n"] += 1
        return await original(status)

    monkeypatch.setattr(store, "get_by_status", counting)
    return reads


async def test_repeated_pool_reads_hit_the_database_once(store, monkeypatch):
    await store.add_endpoint(_endpoint("a"))
    reads = _count_reads(store, monkeypatch)

    for _ in range(5):
        assert [e.id for e in await store.get_pool()] == ["a"]

    assert reads["n"] == 1


async def test_adding_an_endpoint_is_visible_immediately(store):
    await store.add_endpoint(_endpoint("a"))
    assert [e.id for e in await store.get_pool()] == ["a"]

    await store.add_endpoint(_endpoint("b"))

    assert sorted(e.id for e in await store.get_pool()) == ["a", "b"]


async def test_removing_an_endpoint_is_visible_immediately(store):
    await store.add_endpoint(_endpoint("a"))
    await store.add_endpoint(_endpoint("b"))
    await store.get_pool()

    await store.remove_endpoint("a")

    assert [e.id for e in await store.get_pool()] == ["b"]


async def test_a_status_change_is_visible_immediately(store):
    await store.add_endpoint(_endpoint("a"))
    await store.add_endpoint(_endpoint("b"))
    await store.get_pool()

    await store.update_status("a", EndpointStatus.IGNORED)
    assert [e.id for e in await store.get_pool()] == ["b"]

    await store.update_status("a", EndpointStatus.VERIFIED)
    assert sorted(e.id for e in await store.get_pool()) == ["a", "b"]


async def test_a_metadata_update_is_visible_immediately(store):
    await store.add_endpoint(_endpoint("a"))
    await store.get_pool()

    await store.update_status(
        "a", EndpointStatus.VERIFIED, {"provider": "openai", "priority": 9}
    )

    (ep,) = await store.get_pool()
    assert ep.metadata["priority"] == 9


async def test_a_metrics_update_is_visible_immediately(store):
    await store.add_endpoint(_endpoint("a"))
    await store.get_pool()

    await store.update_metrics("a", 42.0, 0.5)

    (ep,) = await store.get_pool()
    assert ep.latency_ms == 42.0 and ep.success_rate == 0.5


async def test_a_write_the_store_did_not_make_is_picked_up_after_the_ttl(store):
    await store.add_endpoint(_endpoint("a"))
    await store.get_pool()
    clock = Clock()
    store._pool_cache = PoolCache(ttl_s=5, clock=clock)
    assert [e.id for e in await store.get_pool()] == ["a"]

    # Another process adds a row directly; this store never saw the write.
    if hasattr(store, "_get_conn"):
        conn = await store._get_conn()
        await conn.execute(
            "INSERT INTO endpoints (id, url, status, metadata) VALUES (?, ?, ?, ?)",
            ("sneaky", "http://sneaky.test/v1", EndpointStatus.VERIFIED.value, "{}"),
        )
        await conn.commit()
    else:
        pool = await store.init_pool()
        await pool.execute(
            "INSERT INTO endpoints (id, url, status, metadata) VALUES ($1, $2, $3, $4)",
            "sneaky", "http://sneaky.test/v1", EndpointStatus.VERIFIED.value, "{}",
        )

    assert [e.id for e in await store.get_pool()] == ["a"]  # still the snapshot
    clock.now += 6
    assert sorted(e.id for e in await store.get_pool()) == ["a", "sneaky"]


async def test_get_by_status_and_get_all_never_serve_the_snapshot(store):
    await store.add_endpoint(_endpoint("a"))
    await store.get_pool()
    await store.update_status("a", EndpointStatus.IGNORED)

    assert [e.id for e in await store.get_by_status(EndpointStatus.IGNORED)] == ["a"]
    assert [e.id for e in await store.get_all()] == ["a"]

import pytest
import json
from unittest.mock import AsyncMock, MagicMock, patch
from models import LLMEndpoint, EndpointStatus
from store.pg_store import PostgresStore


@pytest.fixture
def mock_pool_and_conn():
    # Setup mock connection and pool
    mock_conn = MagicMock()
    mock_conn.execute = AsyncMock(return_value="UPDATE 1")
    mock_conn.fetch = AsyncMock(return_value=[])
    mock_conn.fetchrow = AsyncMock(return_value=None)
    mock_conn.fetchval = AsyncMock(return_value=1)

    # Mock transaction context manager
    mock_tx = MagicMock()
    mock_tx.__aenter__ = AsyncMock()
    mock_tx.__aexit__ = AsyncMock()
    mock_conn.transaction = MagicMock(return_value=mock_tx)

    mock_pool = MagicMock()
    # Mock acquire context manager
    mock_acquire = MagicMock()
    mock_acquire.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_acquire.__aexit__ = AsyncMock()
    mock_pool.acquire = MagicMock(return_value=mock_acquire)

    # Execute direct commands on pool
    mock_pool.execute = AsyncMock(return_value="SUCCESS")
    mock_pool.fetch = AsyncMock(return_value=[])
    mock_pool.fetchrow = AsyncMock(return_value=None)
    mock_pool.fetchval = AsyncMock(return_value=1)
    mock_pool.close = AsyncMock()

    return mock_pool, mock_conn


@pytest.mark.asyncio
async def test_postgres_init_db(mock_pool_and_conn):
    mock_pool, mock_conn = mock_pool_and_conn
    store = PostgresStore("postgresql://localhost/dummy")

    with patch("asyncpg.create_pool", new_callable=AsyncMock) as mock_create_pool:
        mock_create_pool.return_value = mock_pool
        await store.init_db()

        # Verify tables creation
        assert mock_conn.execute.call_count >= 5
        # Verify index creation
        assert any(
            "CREATE INDEX" in call[0][0] for call in mock_conn.execute.call_args_list
        )


@pytest.mark.asyncio
async def test_postgres_add_endpoint(mock_pool_and_conn):
    mock_pool, _ = mock_pool_and_conn
    store = PostgresStore("postgresql://localhost/dummy")
    store._pool = mock_pool

    endpoint = LLMEndpoint(
        id="test-ep",
        url="http://localhost:8000",
        status=EndpointStatus.VERIFIED,
        metadata={"region": "us-east-1"},
        latency_ms=120.0,
        success_rate=0.99,
    )

    await store.add_endpoint(endpoint)

    # Verify execute arguments
    sql = mock_pool.execute.call_args[0][0]
    args = mock_pool.execute.call_args[0][1:]
    assert "INSERT INTO endpoints" in sql
    assert "ON CONFLICT (id) DO UPDATE" in sql
    assert args[0] == "test-ep"
    assert args[1] == "http://localhost:8000/"
    assert args[2] == EndpointStatus.VERIFIED.value
    assert json.loads(args[3]) == {"region": "us-east-1"}


@pytest.mark.asyncio
async def test_postgres_update_status(mock_pool_and_conn):
    mock_pool, _ = mock_pool_and_conn
    store = PostgresStore("postgresql://localhost/dummy")
    store._pool = mock_pool

    # 1. Update with metadata
    await store.update_status("test-ep", EndpointStatus.FOUND, {"latency_ms": 250.0})
    sql = mock_pool.execute.call_args[0][0]
    args = mock_pool.execute.call_args[0][1:]
    assert "UPDATE endpoints SET status = $1, metadata = $2" in sql
    assert "TO_CHAR(NOW()" in sql
    assert args[0] == EndpointStatus.FOUND.value
    assert args[2] == 250.0

    # 2. Update without metadata
    await store.update_status("test-ep", EndpointStatus.FOUND)
    sql_no_meta = mock_pool.execute.call_args[0][0]
    args_no_meta = mock_pool.execute.call_args[0][1:]
    assert "UPDATE endpoints SET status = $1" in sql_no_meta
    assert "last_verified = TO_CHAR" in sql_no_meta
    assert args_no_meta[0] == EndpointStatus.FOUND.value
    assert args_no_meta[1] == "test-ep"


@pytest.mark.asyncio
async def test_postgres_set_and_get_state(mock_pool_and_conn):
    mock_pool, _ = mock_pool_and_conn
    store = PostgresStore("postgresql://localhost/dummy")
    store._pool = mock_pool

    # Test set_state
    await store.set_state("mykey", {"a": 1})
    sql = mock_pool.execute.call_args[0][0]
    args = mock_pool.execute.call_args[0][1:]
    assert "INSERT INTO app_state" in sql
    assert "ON CONFLICT (key) DO UPDATE" in sql
    assert args[0] == "mykey"
    assert json.loads(args[1]) == {"a": 1}

    # Test get_state (miss)
    mock_pool.fetchrow.return_value = None
    res = await store.get_state("mykey", default="default-val")
    assert res == "default-val"

    # Test get_state (hit)
    mock_pool.fetchrow.return_value = ['{"b": 2}']
    res_hit = await store.get_state("mykey")
    assert res_hit == {"b": 2}


@pytest.mark.asyncio
async def test_postgres_query_spend(mock_pool_and_conn):
    mock_pool, _ = mock_pool_and_conn
    store = PostgresStore("postgresql://localhost/dummy")
    store._pool = mock_pool

    mock_pool.fetch.return_value = [
        {"model": "gpt-4o", "requests": 10, "total_cost_usd": 0.05}
    ]

    res = await store.query_spend(
        date_from="2026-06-01", date_to="2026-06-02", group_by="model", limit=20
    )
    assert len(res) == 1
    assert res[0]["model"] == "gpt-4o"

    sql = mock_pool.fetch.call_args[0][0]
    args = mock_pool.fetch.call_args[0][1:]
    assert "SELECT model" in sql
    assert "FROM spend_log" in sql
    assert "date >= $1" in sql
    assert "date <= $2" in sql
    assert "LIMIT $3" in sql
    assert args[0] == "2026-06-01"
    assert args[1] == "2026-06-02"
    assert args[2] == 20


@pytest.mark.asyncio
async def test_postgres_log_audit_chain(mock_pool_and_conn):
    mock_pool, _ = mock_pool_and_conn
    store = PostgresStore("postgresql://localhost/dummy")
    store._pool = mock_pool

    # First write (genesis)
    mock_pool.fetchrow.return_value = None
    await store.log_audit(
        ts=1700000000,
        req_id="req1",
        session_id="sess1",
        key_prefix="sk-tok",
        model="gpt-4o",
        provider="openai",
        status=200,
        prompt_tokens=10,
        completion_tokens=5,
        cost_usd=0.01,
        latency_ms=150.0,
    )

    sql = mock_pool.execute.call_args[0][0]
    args = mock_pool.execute.call_args[0][1:]
    assert "INSERT INTO audit_log" in sql
    assert args[15] == "GENESIS"  # prev_hash is GENESIS
    first_hash = args[14]
    assert len(first_hash) == 64  # valid SHA-256 hash string

    # Second write (linked)
    mock_pool.fetchrow.return_value = [first_hash]
    await store.log_audit(
        ts=1700000100,
        req_id="req2",
        session_id="sess1",
        key_prefix="sk-tok",
        model="gpt-4o",
        provider="openai",
        status=200,
        prompt_tokens=15,
        completion_tokens=10,
        cost_usd=0.02,
        latency_ms=180.0,
    )

    args2 = mock_pool.execute.call_args[0][1:]
    assert args2[15] == first_hash  # prev_hash links to first entry_hash


@pytest.mark.asyncio
async def test_postgres_delete_subject_data(mock_pool_and_conn):
    mock_pool, mock_conn = mock_pool_and_conn
    store = PostgresStore("postgresql://localhost/dummy")
    store._pool = mock_pool

    mock_conn.execute.side_effect = ["DELETE 5", "DELETE 2", "DELETE 1"]

    res = await store.delete_subject_data("sess1")
    assert res["audit_deleted"] == 5
    assert res["spend_deleted"] == 2
    assert res["roles_deleted"] == 1


@pytest.mark.asyncio
async def test_postgres_health_check(mock_pool_and_conn):
    mock_pool, _ = mock_pool_and_conn
    store = PostgresStore("postgresql://localhost/dummy")
    store._pool = mock_pool

    # 1. Success check
    mock_pool.fetchval.return_value = 1
    assert await store.health_check() is True

    # 2. Failure check
    mock_pool.fetchval.side_effect = Exception("db connection error")
    assert await store.health_check() is False
    assert store._pool is None  # pool reset on failure

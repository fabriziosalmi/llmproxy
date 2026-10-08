"""Small failures that took more down than they should have.

* An empty YAML section (``endpoints:``) parses as None; the validator called
  ``.items()`` on it and the process exited blaming "a defect in the validator".
* SQLite ``INSERT OR REPLACE`` deleted any *other* endpoint holding the same URL
  (the column is UNIQUE) where Postgres raised; the route did not check either.
* One unreadable endpoint row (bad JSON, NULL metadata, an out-of-enum status)
  made ``get_pool`` raise, which took every request down.
* ``GET /api/v1/audit?from=garbage`` was a 500.
* ``main.py`` validated ``CONFIG_FILE`` and then ran ``config.yaml``.
"""

import ast
import logging
import pathlib
import sqlite3

import httpx
import pytest

from core.startup_checks import StartupError, validate_config
from models import EndpointStatus, LLMEndpoint
from store.sql_store import SQLiteStore
from tests.test_request_path_hangs_and_gaps import ADMIN_KEY, _two_tier_agent

ROOT = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _keys(monkeypatch):
    monkeypatch.setenv("LLM_PROXY_API_KEYS", "sk-proxy-test")


# ── the validator ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("section", ["endpoints", "server", "security", "caching"])
def test_an_empty_section_is_not_a_validator_crash(section):
    cfg = {"server": {"port": 8090}, "endpoints": {}}
    cfg[section] = None

    assert isinstance(validate_config(cfg), list)


def test_an_empty_nested_section_is_not_a_crash_either():
    assert isinstance(validate_config({"server": {"port": 8090, "tls": None, "metrics": None}}), list)


def test_a_section_of_the_wrong_type_is_reported_by_name():
    with pytest.raises(StartupError, match="'endpoints' must be a mapping"):
        validate_config({"server": {"port": 8090}, "endpoints": ["a", "b"]})


# ── the store ─────────────────────────────────────────────────────────────────


@pytest.fixture
async def store(tmp_path):
    s = SQLiteStore(str(tmp_path / "e.db"))
    await s.init_db()
    yield s
    await s.close()


def _ep(id_, url, status=EndpointStatus.VERIFIED):
    return LLMEndpoint(id=id_, url=url, status=status, metadata={"provider": "openai"})


async def test_a_second_id_for_the_same_url_is_refused_and_destroys_nothing(store):
    await store.add_endpoint(_ep("first", "http://a.example.com/v1"))

    with pytest.raises(sqlite3.IntegrityError):
        await store.add_endpoint(_ep("second", "http://a.example.com/v1"))

    assert [e.id for e in await store.get_all()] == ["first"]


async def test_the_same_id_is_still_updated_in_place(store):
    await store.add_endpoint(_ep("first", "http://a.example.com/v1"))
    await store.add_endpoint(_ep("first", "http://b.example.com/v1"))

    only = await store.get_all()
    assert [(e.id, str(e.url)) for e in only] == [("first", "http://b.example.com/v1")]


async def test_one_unreadable_row_does_not_take_the_registry_down(store, caplog):
    await store.add_endpoint(_ep("good", "http://good.example.com/v1"))
    conn = await store._get_conn()
    for ident, url, status, meta in (
        ("badjson", "http://x.example.com/v1", 3, "{not json"),
        ("nullmeta", "http://y.example.com/v1", 3, None),
        ("badurl", "not a url", 3, "{}"),
    ):
        await conn.execute(
            "INSERT INTO endpoints (id, url, status, metadata) VALUES (?,?,?,?)",
            (ident, url, status, meta),
        )
    await conn.commit()
    store._pool_cache.invalidate()

    with caplog.at_level(logging.WARNING, logger="llmproxy.store"):
        everything = await store.get_all()
        pool = await store.get_pool()

    ids = {e.id for e in everything}
    assert "good" in ids and "badjson" not in ids and "badurl" not in ids
    assert {e.id for e in pool} >= {"good"}
    assert "badjson" in caplog.text and "badurl" in caplog.text
    # a NULL metadata is merely empty, not unreadable
    assert "nullmeta" in ids


# ── the routes ────────────────────────────────────────────────────────────────


async def _client(agent):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=agent.app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {ADMIN_KEY}"},
    )


async def test_a_malformed_audit_date_is_a_400_not_a_500():
    agent = _two_tier_agent()
    async with await _client(agent) as c:
        for param in ("from", "to"):
            resp = await c.get(f"/api/v1/audit?{param}=garbage")
            assert resp.status_code == 400, (param, resp.text)
            assert param in resp.json()["detail"]


async def test_registering_a_url_under_a_second_id_is_a_409():
    agent = _two_tier_agent()
    body = {"id": "one", "url": "https://api.example.com/v1", "provider": "openai"}
    async with await _client(agent) as c:
        first = await c.post("/api/v1/registry", json=body)
        second = await c.post("/api/v1/registry", json={**body, "id": "two"})

    assert first.status_code == 200, first.text
    assert second.status_code == 409
    assert "one" in second.json()["detail"]
    assert "two" not in (agent.config.get("endpoints") or {})


# ── main.py ───────────────────────────────────────────────────────────────────


def test_main_runs_the_config_file_it_validated():
    tree = ast.parse((ROOT / "main.py").read_text())
    calls = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "ProxyOrchestrator"
    ]
    assert calls, "main.py no longer builds a ProxyOrchestrator"
    for call in calls:
        assert any(k.arg == "config_path" for k in call.keywords), (
            "ProxyOrchestrator(store) loads config.yaml whatever CONFIG_FILE says"
        )


async def test_a_failed_supply_chain_check_exits_non_zero(monkeypatch):
    """An aborted start returned normally, so the process exited 0 and a supervisor
    read it as a clean exit."""
    import importlib

    import scripts.verify_deps as verify_deps

    monkeypatch.setattr(verify_deps, "verify_all", lambda strict=False: False)
    main_module = importlib.import_module("main")

    with pytest.raises(SystemExit) as caught:
        await main_module.main()

    assert caught.value.code == 1

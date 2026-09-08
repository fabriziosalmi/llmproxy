"""Config editing from the Admin UI: /api/v1/config/{raw,validate,apply}.

The apply path must NEVER write an invalid config, and validate is a pure
dry-run. Admin auth is exercised via auth_enabled=False (open in dev mode);
the auth gate itself is covered by the existing admin-auth tests."""
import pytest
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport
from unittest.mock import MagicMock

from tests.conftest import InMemoryRepository, minimal_config


def _app(config_path: str = "config.yaml"):
    agent = MagicMock()
    agent.config = minimal_config(auth_enabled=False)
    agent.store = InMemoryRepository()
    agent.config_path = config_path
    app = FastAPI()
    from proxy.routes.config import create_router as config

    app.include_router(config(agent))
    return app, agent


async def _post(app, path, payload):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        return await c.post(path, json=payload)


async def _get(app, path):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        return await c.get(path)


@pytest.mark.asyncio
async def test_validate_rejects_broken_yaml():
    app, _ = _app()
    r = await _post(app, "/api/v1/config/validate", {"yaml": "key: [unclosed"})
    assert r.status_code == 200
    data = r.json()
    assert data["valid"] is False
    assert data["errors"]


@pytest.mark.asyncio
async def test_validate_rejects_non_mapping_root():
    app, _ = _app()
    r = await _post(app, "/api/v1/config/validate", {"yaml": "- a\n- b\n"})
    assert r.json()["valid"] is False


@pytest.mark.asyncio
async def test_validate_accepts_good_config():
    app, _ = _app()
    good = "server:\n  auth:\n    enabled: false\nendpoints: {}\n"
    r = await _post(app, "/api/v1/config/validate", {"yaml": good})
    assert r.status_code == 200
    assert r.json()["valid"] is True


@pytest.mark.asyncio
async def test_validate_too_large_is_rejected():
    app, _ = _app()
    huge = "x: " + "a" * (256 * 1024 + 10)
    r = await _post(app, "/api/v1/config/validate", {"yaml": huge})
    assert r.status_code == 413


@pytest.mark.asyncio
async def test_apply_invalid_does_not_write(tmp_path):
    cfg = tmp_path / "config.yaml"
    original = "server:\n  port: 8090\n"
    cfg.write_text(original)
    app, _ = _app(str(cfg))
    # Broken YAML fails validation → 400 before any write, file untouched.
    r = await _post(app, "/api/v1/config/apply", {"yaml": "key: [unclosed"})
    assert r.status_code == 400
    assert cfg.read_text() == original

    # The error envelope must be shaped like every other one in the package:
    # `detail` is a human-readable string, never an object. This route used to
    # be the single exception among 87 raise sites, so no client could parse
    # errors uniformly. The structured reasons live alongside it, not inside it.
    body = r.json()
    assert isinstance(body.get("detail"), str), (
        f"`detail` must be a string, got {type(body.get('detail')).__name__}"
    )
    assert isinstance(body.get("errors"), list) and body["errors"], (
        "validation reasons must still be returned, at the top level"
    )
    assert isinstance(body.get("warnings"), list)


@pytest.mark.asyncio
async def test_raw_returns_on_disk_source(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("server:\n  port: 8090\n")
    app, _ = _app(str(cfg))
    r = await _get(app, "/api/v1/config/raw")
    assert r.status_code == 200
    assert "port: 8090" in r.json()["yaml"]

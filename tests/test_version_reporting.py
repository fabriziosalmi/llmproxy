"""The proxy must never report a version it does not have.

VERSION was read in three places with three different fallbacks — "0.0.0" in
the app factory, "unknown" in the SIEM webhook records, and "0.1.0-alpha" on
/api/v1/version. The last one is the dangerous shape: it is a plausible
release number, so an operator or a fleet inventory reading that endpoint on
a deployment without a VERSION file is told something specific and false.
"""

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from unittest.mock import MagicMock

from tests.conftest import InMemoryRepository, minimal_config


def _app():
    agent = MagicMock()
    agent.config = minimal_config(auth_enabled=False)
    agent.store = InMemoryRepository()
    app = FastAPI()
    from proxy.routes.admin import create_router

    app.include_router(create_router(agent))
    return app


def _clear_cache():
    from core.version import get_version

    get_version.cache_clear()


def test_version_endpoint_reports_the_version_file():
    from pathlib import Path

    from core.version import get_version

    _clear_cache()
    expected = (Path(__file__).resolve().parent.parent / "VERSION").read_text().strip()
    assert get_version() == expected


@pytest.mark.asyncio
async def test_missing_version_file_is_unknown_not_a_made_up_release(monkeypatch):
    import core.version as v

    monkeypatch.setattr(v, "_VERSION_FILE", "/nonexistent/VERSION")
    _clear_cache()
    try:
        async with AsyncClient(
            transport=ASGITransport(app=_app()), base_url="http://t"
        ) as c:
            body = (await c.get("/api/v1/version")).json()
        assert body["version"] == "unknown", (
            f"reported {body['version']!r} for a deployment with no VERSION file — "
            f"a fallback that looks like a release is worse than no answer"
        )
    finally:
        _clear_cache()


def test_every_version_reader_agrees(monkeypatch):
    """Three readers, one answer — including when the file is gone."""
    import core.version as v
    from core.webhooks import _proxy_version
    from proxy.app_factory import _read_version

    monkeypatch.setattr(v, "_VERSION_FILE", "/nonexistent/VERSION")
    _clear_cache()
    try:
        assert _read_version() == "unknown"
        assert _proxy_version() == "unknown"
        assert v.get_version() == "unknown"
    finally:
        _clear_cache()

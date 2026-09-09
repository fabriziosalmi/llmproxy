"""A working proxy reported itself dead, and the container check could not fail.

Found while smoke-testing the release on the CI box, not by reading code: the
freshly deployed container answered every request correctly — auth enforced,
/v1/models responding, every component "ok" — while GET /health returned
{"status": "down"}. The 1.34.0 image did the same on a cold start, so this
predates the release rather than being introduced by it.

Two independent defects, either of which alone would have hidden the other.

1. The upstream aiohttp session is created LAZILY, on the first forward
   (RotatorAgent._get_session). The health route treated "not created yet" as
   "down", and `session` is in the critical set, so the whole verdict was
   `down` until something forwarded. For a readiness gate that is a deadlock:
   no traffic means no session, no session means never ready, never ready
   means no traffic. For anything else keyed on the field it is simply wrong.

2. The Dockerfile's HEALTHCHECK was `urllib.request.urlopen('/health')` and
   nothing more. /health returns 200 whatever it finds, so the check passed
   with every component down — which is exactly why `docker ps` showed
   `healthy` for four hours while the body said `down`. A health check that
   cannot fail is not a health check.
"""

import ast
import pathlib
import re

import pytest
from httpx import ASGITransport, AsyncClient

from tests.test_coverage_routes import _make_app_with_routes

ROOT = pathlib.Path(__file__).resolve().parent.parent


async def _health(app):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/health")
    assert resp.status_code == 200
    return resp.json()


def _app_with_session(session):
    from proxy.routes.telemetry import create_router

    app, agent = _make_app_with_routes(create_router)
    agent._session = session
    return app


# ── the endpoint's verdict ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_proxy_that_has_not_forwarded_yet_is_not_down():
    """The exact state of every freshly started process, and of every replica
    that has not yet received traffic."""
    data = await _health(_app_with_session(None))

    assert data["components"]["session"]["status"] == "idle"
    assert data["status"] != "down", data["components"]


@pytest.mark.asyncio
async def test_idle_is_reported_as_itself_not_dressed_up_as_ok():
    """Not a whitewash: the state is named, so an operator can tell a proxy
    that has never forwarded from one with a live upstream session."""
    session = (await _health(_app_with_session(None)))["components"]["session"]

    assert session["status"] == "idle"
    assert "first upstream forward" in session.get("detail", "")


@pytest.mark.asyncio
async def test_session_active_still_reports_the_fact():
    """The top-level back-compat field keeps meaning what it says: there is no
    live session. Only the VERDICT changes."""
    assert (await _health(_app_with_session(None)))["session_active"] is False


@pytest.mark.asyncio
async def test_a_closed_session_is_still_down():
    """The distinction the fix rests on. A session that exists and is closed
    means something closed it under a running process — a real failure, and it
    must not be softened into "idle" along with the lazy case."""

    class _Closed:
        closed = True

    data = await _health(_app_with_session(_Closed()))

    assert data["components"]["session"]["status"] == "down"
    assert data["status"] == "down"


@pytest.mark.asyncio
async def test_a_live_session_is_ok():
    class _Live:
        closed = False

    data = await _health(_app_with_session(_Live()))

    assert data["components"]["session"]["status"] == "ok"
    assert data["status"] == "ok", data["components"]


# ── the container check ─────────────────────────────────────────────────────


def _healthcheck_command() -> str:
    src = (ROOT / "Dockerfile").read_text()
    joined = src.replace("\\\n", "")
    line = [ln for ln in joined.splitlines() if ln.startswith("HEALTHCHECK")][0]
    return line.split('python -c "', 1)[1].rstrip('"')


def test_the_container_check_reads_the_body():
    """urlopen() alone passes on any 200, and /health returns 200 whatever it
    finds — so the old check reported healthy with every component down."""
    cmd = _healthcheck_command()

    assert "json.load" in cmd, "the check still ignores the response body"
    assert "status" in cmd


def test_the_container_check_can_fail():
    cmd = _healthcheck_command()

    assert re.search(r"sys\.exit\(1\)", cmd), "no failing exit path"
    assert "'down'" in cmd or '"down"' in cmd


def test_the_container_check_is_syntactically_valid():
    """It is a string inside a Dockerfile: nothing else would catch a typo
    until an image was built, deployed, and observed to be permanently
    unhealthy."""
    ast.parse(_healthcheck_command())


def test_the_container_check_has_its_own_timeout():
    """Without one, urlopen inherits the global default (none), so a hung
    endpoint would leave the check hanging past HEALTHCHECK --timeout with a
    stray process per interval."""
    assert "timeout=" in _healthcheck_command()


def test_degraded_does_not_restart_the_container():
    """Degraded means serving with something reduced. Restarting would not fix
    it, and a restart loop on a degraded-but-working proxy is worse than the
    degradation."""
    assert "degraded" not in _healthcheck_command()

"""Four ways a request could hang, crash, or reach the wrong bag.

Each of these was found by tracing a path rather than by a failing test, which
is why each gets one here:

  * the Tailscale identity lookup ran with aiohttp's 300-second default on the
    chat path, guarded only by the socket FILE existing;
  * the telemetry auth closures verified the inference bag while the middleware
    in front of them verified the admin bag, so the log stream was unreachable
    by any credential in a segregated deployment;
  * the standalone Prometheus exporter bound every interface with no auth, on a
    listener no middleware can reach;
  * nothing bounded body *shape*, so a 200 KB body of nested arrays raised
    RecursionError out of the JSON parser as an unhandled 500.
"""

import asyncio

import httpx
import pytest

from conftest import InMemoryRepository, minimal_config
from test_e2e import LightweightAgent

INFERENCE_KEY = "sk-proxy-inference-only"
ADMIN_KEY = "sk-admin-control-plane"


def _two_tier_agent():
    """An agent with the two key bags genuinely separated."""
    import os

    from core.infisical import clear_cache
    from proxy.app_factory import create_app

    os.environ["LLM_PROXY_API_KEYS"] = INFERENCE_KEY
    os.environ["LLM_PROXY_ADMIN_KEYS"] = ADMIN_KEY
    clear_cache()

    config = minimal_config()
    config["server"]["auth"]["enabled"] = True
    config["server"]["auth"]["api_keys_env"] = "LLM_PROXY_API_KEYS"
    config["server"]["auth"]["admin_keys_env"] = "LLM_PROXY_ADMIN_KEYS"
    agent = LightweightAgent(InMemoryRepository(), config)
    agent.app = create_app(agent)
    return agent


# ── 1. the Tailscale lookup is bounded ──────────────────────────────────────


def test_the_tailscale_session_carries_a_timeout(monkeypatch, tmp_path):
    """The finding: ClientSession was built with a connector and nothing else.

    aiohttp's default is 300 seconds, and this runs on the chat path.
    """
    import aiohttp

    from core.zero_trust import TAILSCALE_API_TIMEOUT_S, ZeroTrustManager

    captured = {}

    class _FakeSession:
        closed = False

        def __init__(self, *args, **kwargs):
            captured.update(kwargs)

        def get(self, *args, **kwargs):  # pragma: no cover - never awaited
            raise AssertionError("test should not reach the request")

    socket = tmp_path / "tailscaled.sock"
    socket.write_text("")

    monkeypatch.setattr(aiohttp, "ClientSession", _FakeSession)
    monkeypatch.setattr(aiohttp, "UnixConnector", lambda path: object())

    zt = ZeroTrustManager({"security": {"zero_trust": {"enabled": False}}})
    zt.ts_socket = str(socket)
    asyncio.run(zt.verify_tailscale_identity("100.64.0.1"))

    timeout = captured.get("timeout")
    assert timeout is not None, "session built without a timeout — 300s applies"
    assert timeout.total == TAILSCALE_API_TIMEOUT_S
    assert TAILSCALE_API_TIMEOUT_S <= 5, (
        "a unix-socket round trip on the request path must fail fast"
    )


def test_a_stalled_tailscale_daemon_returns_unverified(monkeypatch, tmp_path):
    """A timeout must degrade to 'unverified', not propagate.

    The identity is advisory here — used for attribution, not authorisation —
    so the correct answer to a stalled daemon is the one the code already
    returns for a socket error.
    """
    import aiohttp

    from core.zero_trust import ZeroTrustManager

    class _TimingOutSession:
        closed = False

        def __init__(self, *args, **kwargs):
            pass

        def get(self, *args, **kwargs):
            raise asyncio.TimeoutError()

    socket = tmp_path / "tailscaled.sock"
    socket.write_text("")

    monkeypatch.setattr(aiohttp, "ClientSession", _TimingOutSession)
    monkeypatch.setattr(aiohttp, "UnixConnector", lambda path: object())

    zt = ZeroTrustManager({"security": {"zero_trust": {"enabled": False}}})
    zt.ts_socket = str(socket)
    result = asyncio.run(zt.verify_tailscale_identity("100.64.0.1"))

    assert result == {"status": "unverified", "reason": "api_timeout"}


# ── 2. the log stream is reachable by the credential that guards it ─────────


@pytest.mark.asyncio
async def test_an_admin_key_can_mint_an_sse_token():
    """The finding, stated directly: this returned 401 to every credential.

    The middleware verifies the admin bag; the route verified the inference
    bag. An admin key passed the first and failed the second, an inference key
    failed the first. Minting is the only way a browser can subscribe, so the
    live log panel was dark in every segregated deployment.
    """
    agent = _two_tier_agent()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=agent.app), base_url="http://test"
    ) as c:
        resp = await c.post(
            "/api/v1/logs/token", headers={"Authorization": f"Bearer {ADMIN_KEY}"}
        )

    assert resp.status_code == 200, resp.text
    assert resp.json()["sse_token"]


@pytest.mark.asyncio
async def test_an_inference_key_still_cannot_mint_one():
    """Fixing reachability must not reopen the tier."""
    agent = _two_tier_agent()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=agent.app), base_url="http://test"
    ) as c:
        resp = await c.post(
            "/api/v1/logs/token", headers={"Authorization": f"Bearer {INFERENCE_KEY}"}
        )

    assert resp.status_code == 401


def test_the_sse_fallback_secret_is_not_a_published_constant():
    """It was the literal "llmproxy-dev-sse-secret", readable in this repo."""
    from proxy.routes.telemetry import _FALLBACK_SSE_SECRET

    assert _FALLBACK_SSE_SECRET != "llmproxy-dev-sse-secret"
    assert len(_FALLBACK_SSE_SECRET) >= 32


# ── 3. the unauthenticated exporter does not bind the world by default ──────


def test_the_metrics_exporter_defaults_to_loopback():
    """This listener is outside the ASGI app — no middleware reaches it.

    It serves the same registry that GET /metrics is in _ALSO_PROTECT to guard.
    """
    from core.metrics import DEFAULT_METRICS_BIND

    assert DEFAULT_METRICS_BIND == "127.0.0.1"


def test_widening_the_exporter_bind_warns(monkeypatch, caplog):
    """Publishing it is legitimate in a pod; doing it silently is not."""
    import core.metrics as metrics

    monkeypatch.setattr(metrics, "start_http_server", lambda port, addr: None)
    with caplog.at_level("WARNING"):
        metrics.start_metrics_server(port=9091, addr="0.0.0.0")  # nosec B104

    assert any("no authentication" in r.message for r in caplog.records)


def test_loopback_bind_does_not_warn(monkeypatch, caplog):
    import core.metrics as metrics

    monkeypatch.setattr(metrics, "start_http_server", lambda port, addr: None)
    with caplog.at_level("WARNING"):
        metrics.start_metrics_server(port=9091)

    assert not any("no authentication" in r.message for r in caplog.records)


# ── 4. body shape is bounded, not just body size ────────────────────────────


def test_nesting_depth_counts_structure():
    from core.firewall_asgi import max_nesting_depth

    assert max_nesting_depth(b"{}") == 1
    assert max_nesting_depth(b'{"a": [1, 2, {"b": 3}]}') == 3
    assert max_nesting_depth(b"[" * 500 + b"]" * 500) == 500


def test_nesting_depth_ignores_brackets_inside_strings():
    """A prompt containing "[[[[" is content, not structure."""
    from core.firewall_asgi import max_nesting_depth

    body = b'{"messages": [{"role": "user", "content": "[[[[[[[[[["}]}'
    assert max_nesting_depth(body) == 3


def test_nesting_depth_ignores_escaped_quotes():
    from core.firewall_asgi import max_nesting_depth

    assert max_nesting_depth(b'{"a": "he said \\" [[[ "}') == 1


@pytest.mark.asyncio
async def test_a_deeply_nested_body_is_refused_not_a_recursionerror():
    """The finding: 100k nested arrays is ~200 KB, under the size cap.

    Before this it raised RecursionError straight out of json.loads — an
    unhandled 500 with a traceback on a path any caller can reach.
    """
    from proxy.app_factory import create_app

    config = minimal_config()
    config["server"]["auth"]["enabled"] = False
    agent = LightweightAgent(InMemoryRepository(), config)
    agent.app = create_app(agent)

    deep = b"[" * 100_000 + b"]" * 100_000

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=agent.app), base_url="http://test"
    ) as c:
        resp = await c.post(
            "/v1/chat/completions",
            content=deep,
            headers={"Content-Type": "application/json"},
        )

    assert resp.status_code == 400, resp.text
    assert resp.json()["error"] == "malformed_body"


@pytest.mark.asyncio
async def test_an_ordinary_body_is_unaffected():
    """The bound must be generous enough that real requests never see it."""
    import json

    from proxy.app_factory import create_app

    config = minimal_config()
    config["server"]["auth"]["enabled"] = False
    agent = LightweightAgent(InMemoryRepository(), config)
    agent.app = create_app(agent)

    body = json.dumps(
        {
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "hello"}],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "f",
                        "parameters": {
                            "type": "object",
                            "properties": {"a": {"type": "array", "items": {}}},
                        },
                    },
                }
            ],
        }
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=agent.app), base_url="http://test"
    ) as c:
        resp = await c.post(
            "/v1/chat/completions",
            content=body,
            headers={"Content-Type": "application/json"},
        )

    assert resp.status_code != 400 or resp.json().get("error") != "malformed_body"

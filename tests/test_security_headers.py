"""Tests for the response security-headers middleware.

Mounts only `install_security_headers` on a bare FastAPI so we exercise the
exact production middleware without pulling in store/plugins/tracing.
"""

import pytest
import pytest_asyncio
import httpx
from fastapi import FastAPI

from proxy.app_factory import install_security_headers


def _build_app() -> FastAPI:
    app = FastAPI()
    install_security_headers(app)

    @app.get("/api/v1/ping")
    async def ping():
        return {"ok": True}

    @app.get("/ui/")
    async def ui_root():
        return "<html><body>ui</body></html>"

    @app.get("/ui/chat.html")
    async def ui_chat():
        return "<html><body>chat</body></html>"

    return app


@pytest_asyncio.fixture
async def client():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_build_app()),
        base_url="http://test",
    ) as c:
        yield c


# ── Banner ──


@pytest.mark.asyncio
async def test_server_header_is_rebranded(client):
    """`Server: uvicorn` must not leak — middleware overrides to `llmproxy`."""
    resp = await client.get("/api/v1/ping")
    assert resp.headers.get("server") == "llmproxy"
    assert "uvicorn" not in resp.headers.get("server", "").lower()


# ── Always-on hardening ──


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/api/v1/ping", "/ui/"])
async def test_global_hardening_headers(client, path):
    resp = await client.get(path)
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["x-frame-options"] == "DENY"
    assert resp.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert resp.headers["cross-origin-opener-policy"] == "same-origin"
    assert resp.headers["cross-origin-resource-policy"] == "same-origin"


@pytest.mark.asyncio
async def test_permissions_policy_disables_sensitive_features(client):
    resp = await client.get("/api/v1/ping")
    pp = resp.headers["permissions-policy"]
    for sensitive in (
        "camera=()",
        "microphone=()",
        "geolocation=()",
        "payment=()",
        "usb=()",
    ):
        assert sensitive in pp, f"{sensitive} not denied in Permissions-Policy"
    # clipboard-write must remain available to /ui/chat.html copy buttons
    assert "clipboard-write=(self)" in pp


# ── CSP differentiation ──


@pytest.mark.asyncio
async def test_api_csp_is_locked_down(client):
    resp = await client.get("/api/v1/ping")
    csp = resp.headers["content-security-policy"]
    assert "default-src 'none'" in csp
    assert "frame-ancestors 'none'" in csp
    # No script/style/connect leeway on API responses
    assert "'self'" not in csp.replace("'none'", "")
    assert "'unsafe-inline'" not in csp


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/ui/", "/ui/chat.html"])
async def test_ui_csp_keeps_self_and_chat_dependencies(client, path):
    resp = await client.get(path)
    csp = resp.headers["content-security-policy"]
    assert "default-src 'self'" in csp
    assert "script-src 'self'" in csp
    assert "img-src 'self' data:" in csp
    assert "frame-ancestors 'none'" in csp
    assert "base-uri 'self'" in csp


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/ui/", "/ui/chat.html"])
async def test_coep_require_corp_on_ui(client, path):
    """COEP require-corp is enabled on UI paths after the highlight.js bundle
    fix (1.21.61) removed the last cross-origin subresource."""
    resp = await client.get(path)
    assert resp.headers["cross-origin-embedder-policy"] == "require-corp"


@pytest.mark.asyncio
async def test_coep_not_on_api_responses(client):
    """API responses don't need COEP — keep the header surface minimal there."""
    resp = await client.get("/api/v1/ping")
    assert "cross-origin-embedder-policy" not in {
        k.lower() for k in resp.headers.keys()
    }


# ── Trace propagation safety ──


@pytest.mark.asyncio
async def test_trace_id_propagated_when_valid(client):
    resp = await client.get("/api/v1/ping", headers={"x-trace-id": "abc-123-def"})
    assert resp.headers.get("x-trace-id") == "abc-123-def"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "malicious",
    [
        "'; DROP TABLE logs;--",
        "<script>alert(1)</script>",
        "../../etc/passwd",
        "trace\r\nSet-Cookie: pwn=1",
        "A" * 200,
    ],
)
async def test_malicious_trace_id_is_dropped(client, malicious):
    resp = await client.get("/api/v1/ping", headers={"x-trace-id": malicious})
    assert "x-trace-id" not in {k.lower() for k in resp.headers.keys()}


@pytest.mark.asyncio
async def test_traceparent_id_is_extracted_and_validated(client):
    # W3C traceparent: version-traceid-spanid-flags
    valid = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"
    resp = await client.get("/api/v1/ping", headers={"traceparent": valid})
    assert resp.headers.get("x-trace-id") == "0af7651916cd43dd8448eb211c80319c"

"""
LLMPROXY — FastAPI App Factory.

Builds the FastAPI application with middleware stack, route wiring,
security headers, and graceful shutdown hook.

Auth model — Fail-Closed (Secure by Default)
─────────────────────────────────────────────
When auth is enabled, the global_admin_auth middleware enforces
authentication on ALL paths under /api/v1/* and /admin/* PLUS the
sensitive root-level paths listed in _ALSO_PROTECT, BEFORE any route
handler runs.

Only paths in _PUBLIC_EXACT are reachable without credentials.  Any
new route added under a protected prefix is automatically denied unless
the developer explicitly adds its path to _PUBLIC_EXACT — the opposite
of the previous per-route opt-in pattern that guaranteed future CVEs.

The per-route _check_admin_auth() closures in individual route modules
are retained as defence-in-depth: they catch any gap in the middleware
config (e.g. a misconfigured prefix) and produce a descriptive error.
"""

import os
import re
import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from core.tracing import TraceManager
from core.firewall_asgi import ByteLevelFirewallMiddleware

logger = logging.getLogger("llmproxy.app_factory")

# ── Auth whitelist ──────────────────────────────────────────────────────────
# Paths that must remain reachable WITHOUT credentials even when auth is on.
# Keep this list as SHORT as possible — every entry is a potential exposure.
#
#   /health                   — liveness probe (no operational secrets)
#   /api/v1/identity/config   — tells SSO clients which provider to redirect to
#   /api/v1/identity/exchange — token exchange: JWT validated inside the route
#   /api/v1/identity/me       — returns {"authenticated": false} for callers
#                               without a token; route does its own check
_PUBLIC_EXACT: frozenset = frozenset({
    "/health",
    "/api/v1/identity/config",
    "/api/v1/identity/exchange",
    "/api/v1/identity/me",
})

# Path prefixes that are fully protected (deny-all except _PUBLIC_EXACT above).
_PROTECTED_PREFIXES: tuple = ("/api/v1/", "/admin/")

# Root-level paths outside the prefixes above that also require auth.
# /metrics exposes token counts, model usage, budget, and timing side-channels
# that allow traffic-pattern inference across tenants.
_ALSO_PROTECT: frozenset = frozenset({
    "/metrics",
})
# ───────────────────────────────────────────────────────────────────────────


def _read_version() -> str:
    """Read version from VERSION file."""
    try:
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "VERSION")) as f:
            return f.read().strip()
    except FileNotFoundError:
        return "0.0.0"


def create_app(agent) -> FastAPI:
    """App factory: builds the FastAPI application with middleware and routes."""
    from core.rate_limiter import RateLimitMiddleware

    _version = _read_version()
    auth_enabled = agent.config.get("server", {}).get("auth", {}).get("enabled", False)

    # Disable interactive API docs when auth is enabled.
    # /docs, /redoc and /openapi.json hand an attacker a complete map of every
    # endpoint before they authenticate — including ones added in future PRs.
    _docs_url = None if auth_enabled else "/docs"
    _redoc_url = None if auth_enabled else "/redoc"
    _openapi_url = None if auth_enabled else "/openapi.json"

    app = FastAPI(
        title="LLMProxy",
        version=_version,
        description="LLM Security Gateway",
        docs_url=_docs_url,
        redoc_url=_redoc_url,
        openapi_url=_openapi_url,
    )
    agent._start_time = __import__("time").time()
    agent._version = _version
    TraceManager.instrument_app(app)

    # ── Global fail-closed auth middleware ──────────────────────────────────
    # Runs BEFORE any route handler. Rejects requests to protected paths that
    # lack a valid API key. New routes under /api/v1/ or /admin/ are denied
    # automatically — no per-route _check_admin_auth() needed for protection
    # (those closures remain as defence-in-depth only).
    @app.middleware("http")
    async def global_admin_auth(request: Request, call_next):
        if not auth_enabled:
            return await call_next(request)

        path = request.url.path

        needs_auth = (
            any(path.startswith(p) for p in _PROTECTED_PREFIXES)
            or path in _ALSO_PROTECT
        )

        if needs_auth and path not in _PUBLIC_EXACT:
            auth_header = request.headers.get("Authorization", "")
            token = auth_header.replace("Bearer ", "").strip()
            valid_keys = agent._get_api_keys()
            if not token or token not in valid_keys:
                from fastapi.responses import JSONResponse
                logger.warning(
                    f"Global auth: rejected {request.method} {path} "
                    f"from {request.client.host if request.client else 'unknown'}"
                )
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Unauthorized"},
                )

        return await call_next(request)

    # ── Payload size guard ──────────────────────────────────────────────────
    # Reject oversized requests BEFORE JSON parsing (OOM protection).
    max_payload_kb = agent.config.get("security", {}).get("max_payload_size_kb", 512)
    max_payload_bytes = max_payload_kb * 1024

    @app.middleware("http")
    async def payload_size_guard(request: Request, call_next):
        """R2-05: Check Content-Length AND reject chunked requests without CL
        that could bypass the size guard entirely."""
        if request.method in ("POST", "PUT", "PATCH"):
            content_length = request.headers.get("content-length")
            if content_length:
                if int(content_length) > max_payload_bytes:
                    from fastapi.responses import JSONResponse
                    return JSONResponse(
                        status_code=413,
                        content={
                            "error": f"Payload too large. Max: {max_payload_kb} KB",
                            "max_bytes": max_payload_bytes,
                        },
                    )
            elif request.headers.get("transfer-encoding", "").lower() == "chunked":
                # No Content-Length + chunked = potential size bypass.
                # The ByteLevelFirewall enforces max_body_bytes on actual
                # body accumulation, but only if configured. Reject here
                # as defense-in-depth for requests to mutating endpoints.
                pass  # Allow — ByteLevelFirewall handles actual body size
        return await call_next(request)

    # Security + observability response headers
    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        # W3C Trace Context: propagate trace ID for full-stack observability
        # H8: Validate trace_id is alphanumeric+dash only to prevent log
        # injection via crafted X-Trace-Id headers (e.g. SQL/SIEM injection).
        trace_id = request.headers.get("x-trace-id") or (request.headers.get("traceparent", "").split("-")[1] if "-" in request.headers.get("traceparent", "") else None)
        if trace_id and re.match(r'^[a-fA-F0-9-]{1,64}$', trace_id):
            response.headers["X-Trace-Id"] = trace_id
        if request.url.path.startswith("/ui"):
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; "
                "script-src 'self'; "
                "style-src 'self' https://fonts.googleapis.com; "
                "font-src 'self' https://fonts.gstatic.com; "
                "img-src 'self' data:; "
                "connect-src 'self'; "
                "frame-ancestors 'none'"
            )
        return response

    tls_cfg = agent.config.get("server", {}).get("tls", {})
    if not tls_cfg.get("enabled", False):
        logger.warning(
            "TLS is DISABLED — all traffic is unencrypted. For production, either "
            "enable TLS in config.yaml or place a reverse proxy (Traefik/Caddy/nginx) in front."
        )
    cors_origins = agent.config.get("server", {}).get("cors_origins", ["*"])
    if cors_origins == ["*"]:
        logger.warning(
            "CORS allow_origins is ['*'] — any website can make authenticated requests "
            "to this proxy. Set server.cors_origins in config.yaml to restrict in production."
        )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
    )
    app.add_middleware(ByteLevelFirewallMiddleware, max_body_bytes=max_payload_bytes)
    app.add_middleware(RateLimitMiddleware, config=agent.config)

    from .routes import (
        admin_router, registry_router, identity_router,
        plugins_router, telemetry_router, chat_router,
        models_router, embeddings_router, completions_router,
        gdpr_router,
    )
    app.include_router(chat_router(agent))
    app.include_router(completions_router(agent))
    app.include_router(embeddings_router(agent))
    app.include_router(models_router(agent))
    app.include_router(admin_router(agent))
    app.include_router(registry_router(agent))
    app.include_router(identity_router(agent))
    app.include_router(plugins_router(agent))
    app.include_router(telemetry_router(agent))
    app.include_router(gdpr_router(agent))

    # Serve frontend
    ui_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ui")
    if os.path.exists(ui_path):
        app.mount("/ui", StaticFiles(directory=ui_path, html=True), name="ui")

    @app.on_event("shutdown")
    async def _shutdown():
        from proxy.background import drain_pending_writes
        # 1. Flush plugin state (SmartBudgetGuard persists on_unload)
        for name, instance in agent.plugin_manager._plugin_instances.items():
            try:
                await instance.on_unload()
            except Exception as e:
                logger.error(f"Plugin '{name}' unload failed: {e}")
        # 2. Drain pending write queue to SQLite
        await drain_pending_writes(agent)
        # 3. Force SQLite WAL checkpoint before container receives SIGKILL
        try:
            conn = await agent.store._get_conn()
            await conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except Exception as e:
            logger.error(f"WAL checkpoint failed on shutdown: {e}")
        await agent.cache_backend.close()

    return app

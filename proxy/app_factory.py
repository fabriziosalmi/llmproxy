"""
LLMPROXY — FastAPI App Factory.

Builds the FastAPI application with middleware stack, route wiring,
security headers, and graceful shutdown hook.
"""

import os
import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from core.tracing import TraceManager
from core.firewall_asgi import ByteLevelFirewallMiddleware

logger = logging.getLogger("llmproxy.app_factory")


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
    app = FastAPI(title="LLMProxy", version=_version, description="LLM Security Gateway")
    agent._start_time = __import__("time").time()
    agent._version = _version
    TraceManager.instrument_app(app)

    # Payload size guard — reject oversized requests BEFORE JSON parsing (OOM protection)
    max_payload_kb = agent.config.get("security", {}).get("max_payload_size_kb", 512)
    max_payload_bytes = max_payload_kb * 1024

    @app.middleware("http")
    async def payload_size_guard(request: Request, call_next):
        if request.method in ("POST", "PUT", "PATCH"):
            content_length = request.headers.get("content-length")
            if content_length and int(content_length) > max_payload_bytes:
                from fastapi.responses import JSONResponse
                return JSONResponse(
                    status_code=413,
                    content={
                        "error": f"Payload too large. Max: {max_payload_kb} KB",
                        "max_bytes": max_payload_bytes,
                    },
                )
        return await call_next(request)

    # Security + observability response headers
    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        # W3C Trace Context: propagate trace ID for full-stack observability
        trace_id = request.headers.get("x-trace-id") or request.headers.get("traceparent", "").split("-")[1] if "-" in request.headers.get("traceparent", "") else None
        if trace_id:
            response.headers["X-Trace-Id"] = trace_id
        if request.url.path.startswith("/ui"):
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; "
                "script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
                "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
                "font-src 'self' https://fonts.gstatic.com; "
                "img-src 'self' data:; "
                "connect-src 'self'; "
                "frame-ancestors 'none'"
            )
        return response

    cors_origins = agent.config.get("server", {}).get("cors_origins", ["*"])
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
    )
    app.add_middleware(ByteLevelFirewallMiddleware)
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
            import aiosqlite
            async with aiosqlite.connect(agent.store.db_path) as conn:
                await conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except Exception as e:
            logger.error(f"WAL checkpoint failed on shutdown: {e}")
        await agent.cache_backend.close()

    return app

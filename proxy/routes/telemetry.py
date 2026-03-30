"""Telemetry routes: health, metrics, logs SSE stream."""
import re
import json
import asyncio

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import Response, StreamingResponse

# Strip ANSI escape sequences and control chars to prevent terminal injection via xterm.js
_ANSI_RE = re.compile(r'\x1b\[[0-9;]*[a-zA-Z]|\x1b\].*?\x07')
_CTRL_RE = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')

# SSE connection limit — prevents resource exhaustion from stale consumers
_MAX_SSE_CONNECTIONS = 20
_active_log_streams = 0

def _sanitize_log(log: dict) -> dict:
    """Strip terminal escape sequences from log string values."""
    sanitized = {}
    for k, v in log.items():
        if isinstance(v, str):
            v = _ANSI_RE.sub('', v)
            v = _CTRL_RE.sub('', v)
        elif isinstance(v, dict):
            v = _sanitize_log(v)
        sanitized[k] = v
    return sanitized

def create_router(agent) -> APIRouter:
    router = APIRouter()

    def _check_auth(request: Request):
        """Enforce API key auth on sensitive streaming endpoints.

        /api/v1/logs streams all application events including SECURITY-level
        entries (blocked IPs, failed auth attempts, identity assertions, plugin
        installs).  An unauthenticated subscriber gets a real-time recon feed
        that reveals which keys are failing (to time attacks), which IPs are
        blocked (to rotate), and user email addresses from IDENTITY log lines.
        Auth is skipped only when explicitly disabled (development mode).
        """
        if not agent.config.get("server", {}).get("auth", {}).get("enabled", False):
            return
        auth_header = request.headers.get("Authorization", "")
        token = auth_header.replace("Bearer ", "").strip()
        valid_keys = agent._get_api_keys()
        if not token or token not in valid_keys:
            raise HTTPException(status_code=401, detail="Telemetry: Unauthorized")

    @router.get("/health")
    async def health():
        import time as _time
        pool = await agent.store.get_pool()
        healthy_count = 0
        for e in pool:
            if await (await agent.circuit_manager.get_breaker(e.id)).can_execute():
                healthy_count += 1
        uptime = _time.time() - getattr(agent, "_start_time", _time.time())
        return {
            "status": "ok",
            "version": getattr(agent, "_version", "unknown"),
            "uptime_seconds": round(uptime),
            "pool_size": len(pool),
            "pool_healthy": healthy_count,
            "session_active": agent._session is not None and not agent._session.closed,
            "budget_today_usd": round(agent.total_cost_today, 4),
        }

    @router.get("/metrics")
    async def metrics():
        from core.metrics import get_metrics_response
        body, content_type = get_metrics_response()
        return Response(content=body, media_type=content_type)

    @router.get("/api/v1/logs")
    async def stream_logs(request: Request):
        _check_auth(request)
        global _active_log_streams
        if _active_log_streams >= _MAX_SSE_CONNECTIONS:
            raise HTTPException(status_code=503, detail="Too many SSE connections")

        async def log_generator():
            global _active_log_streams
            _active_log_streams += 1
            try:
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        log = await asyncio.wait_for(agent.log_queue.get(), timeout=1.0)
                        yield f"data: {json.dumps(_sanitize_log(log) if isinstance(log, dict) else log)}\n\n"
                    except asyncio.TimeoutError:
                        yield ": keep-alive\n\n"
                    except (asyncio.CancelledError, GeneratorExit):
                        break
            except (asyncio.CancelledError, GeneratorExit):
                pass
            finally:
                _active_log_streams -= 1

        return StreamingResponse(log_generator(), media_type="text/event-stream")

    return router

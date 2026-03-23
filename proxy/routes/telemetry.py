"""Telemetry routes: health, metrics, logs SSE stream."""
import re
import json

from fastapi import APIRouter
from fastapi.responses import Response, StreamingResponse

# Strip ANSI escape sequences and control chars to prevent terminal injection via xterm.js
_ANSI_RE = re.compile(r'\x1b\[[0-9;]*[a-zA-Z]|\x1b\].*?\x07')
_CTRL_RE = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')

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

    @router.get("/health")
    async def health():
        pool = await agent.store.get_pool()
        return {
            "status": "ok",
            "pool_size": len(pool),
            "session_active": agent._session is not None and not agent._session.closed
        }

    @router.get("/metrics")
    async def metrics():
        from core.metrics import get_metrics_response
        body, content_type = get_metrics_response()
        return Response(content=body, media_type=content_type)

    @router.get("/api/v1/logs")
    async def stream_logs():
        async def log_generator():
            while True:
                log = await agent.log_queue.get()
                yield f"data: {json.dumps(_sanitize_log(log) if isinstance(log, dict) else log)}\n\n"
        return StreamingResponse(log_generator(), media_type="text/event-stream")

    return router

"""Telemetry routes: health, metrics, logs SSE stream."""
import json

from fastapi import APIRouter
from fastapi.responses import Response, StreamingResponse

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
                yield f"data: {json.dumps(log)}\n\n"
        return StreamingResponse(log_generator(), media_type="text/event-stream")

    return router

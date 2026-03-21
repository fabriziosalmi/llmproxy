"""Registry routes: endpoint CRUD (toggle, delete, priority, list)."""
from fastapi import APIRouter, Request, HTTPException

from models import EndpointStatus


def create_router(agent) -> APIRouter:
    router = APIRouter()

    @router.post("/api/v1/registry/{endpoint_id}/toggle")
    async def toggle_endpoint(endpoint_id: str):
        all_endpoints = await agent.store.get_all()
        target = next((e for e in all_endpoints if e.id == endpoint_id), None)
        if not target:
            raise HTTPException(status_code=404, detail="Endpoint not found")
        new_status = EndpointStatus.VERIFIED if target.status != EndpointStatus.VERIFIED else EndpointStatus.IGNORED
        await agent.store.update_status(endpoint_id, new_status)
        await agent._add_log(f"ENDPOINT: {endpoint_id} set to {new_status.value}")
        return {"id": endpoint_id, "status": new_status.value}

    @router.get("/api/v1/telemetry/stream")
    async def telemetry_stream(request: Request):
        """Real-time SSE stream for the 'Shadow-ops' HUD."""
        import asyncio
        import json
        from fastapi.responses import StreamingResponse

        async def event_generator():
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(agent.telemetry_queue.get(), timeout=1.0)
                    yield f"data: {json.dumps(event)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
        return StreamingResponse(event_generator(), media_type="text/event-stream")

    @router.delete("/api/v1/registry/{endpoint_id}")
    async def delete_endpoint(endpoint_id: str):
        await agent.store.remove_endpoint(endpoint_id)
        await agent._add_log(f"ENDPOINT: {endpoint_id} DELETED", level="WARNING")
        return {"status": "deleted"}

    @router.post("/api/v1/registry/{endpoint_id}/priority")
    async def set_priority(endpoint_id: str, request: Request):
        data = await request.json()
        priority = data.get("priority", 0)
        all_endpoints = await agent.store.get_all()
        target = next((e for e in all_endpoints if e.id == endpoint_id), None)
        if not target:
            raise HTTPException(status_code=404, detail="Endpoint not found")
        metadata = target.metadata
        metadata["priority"] = priority
        await agent.store.update_status(endpoint_id, target.status, metadata)
        return {"id": endpoint_id, "priority": priority}

    @router.get("/api/v1/registry")
    async def get_registry():
        endpoints = await agent.store.get_all()
        circuit_states = agent.circuit_manager.get_all_states() if hasattr(agent, 'circuit_manager') else {}
        return [{
            "id": e.id,
            "name": e.url.host if e.url.host else str(e.url),
            "url": str(e.url),
            "status": "Live" if e.status == EndpointStatus.VERIFIED else e.status.name,
            "latency": f"{e.latency_ms:.0f}ms" if e.latency_ms else "--",
            "priority": e.metadata.get("priority", 0),
            "type": e.metadata.get("provider_type", "Generic"),
            "circuit_state": circuit_states.get(e.id, {}).get("state", "closed"),
            "failure_count": circuit_states.get(e.id, {}).get("failure_count", 0),
            "failure_threshold": circuit_states.get(e.id, {}).get("failure_threshold", 5),
        } for e in endpoints]

    return router

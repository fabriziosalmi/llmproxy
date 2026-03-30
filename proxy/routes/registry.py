"""Registry routes: endpoint CRUD (toggle, delete, priority, list)."""
from fastapi import APIRouter, Request, HTTPException

from models import EndpointStatus


def create_router(agent) -> APIRouter:
    router = APIRouter()

    def _check_admin_auth(request: Request):
        """Enforce API key auth on all registry mutating endpoints.

        Toggle, delete, and priority changes directly affect which upstream
        providers the proxy uses and are equivalent in impact to admin operations.
        Without auth enforcement an unauthenticated attacker can disable all
        endpoints (instant DoS) or redirect traffic to a malicious provider.
        Mirrors the pattern in admin.py and plugins.py — skipped only when
        auth is explicitly disabled (development mode).
        """
        if not agent.config.get("server", {}).get("auth", {}).get("enabled", False):
            return
        auth_header = request.headers.get("Authorization", "")
        token = auth_header.replace("Bearer ", "").strip()
        valid_keys = agent._get_api_keys()
        if not token or token not in valid_keys:
            raise HTTPException(status_code=401, detail="Registry: Unauthorized")

    @router.post("/api/v1/registry/{endpoint_id}/toggle")
    async def toggle_endpoint(endpoint_id: str, request: Request):
        _check_admin_auth(request)
        all_endpoints = await agent.store.get_all()
        target = next((e for e in all_endpoints if e.id == endpoint_id), None)
        if not target:
            raise HTTPException(status_code=404, detail="Endpoint not found")
        new_status = EndpointStatus.VERIFIED if target.status != EndpointStatus.VERIFIED else EndpointStatus.IGNORED
        await agent.store.update_status(endpoint_id, new_status)
        await agent._add_log(f"ENDPOINT: {endpoint_id} set to {new_status.value}")
        return {"id": endpoint_id, "status": new_status.value}

    # SSE connection limit — prevents resource exhaustion
    _MAX_TELEMETRY_STREAMS = 20
    _active_telemetry = {"count": 0}

    @router.get("/api/v1/telemetry/stream")
    async def telemetry_stream(request: Request):
        _check_admin_auth(request)
        """Real-time SSE stream for the SOC dashboard."""
        import asyncio
        import json
        from fastapi.responses import StreamingResponse

        if _active_telemetry["count"] >= _MAX_TELEMETRY_STREAMS:
            raise HTTPException(status_code=503, detail="Too many SSE connections")

        async def event_generator():
            _active_telemetry["count"] += 1
            try:
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        event = await asyncio.wait_for(agent.telemetry_queue.get(), timeout=1.0)
                        yield f"data: {json.dumps(event)}\n\n"
                    except asyncio.TimeoutError:
                        yield ": keep-alive\n\n"
                    except (asyncio.CancelledError, GeneratorExit):
                        break
            except (asyncio.CancelledError, GeneratorExit):
                pass
            finally:
                _active_telemetry["count"] -= 1

        return StreamingResponse(event_generator(), media_type="text/event-stream")

    @router.post("/api/v1/registry")
    async def add_endpoint_api(request: Request):
        """Add a new LLM endpoint to the registry."""
        _check_admin_auth(request)
        data = await request.json()
        ep_id = data.get("id", "").strip()
        url = data.get("url", "").strip()
        provider = data.get("provider", "openai")
        priority = int(data.get("priority", 0))
        if not ep_id or not url:
            raise HTTPException(status_code=400, detail="id and url are required")
        from models import LLMEndpoint, EndpointStatus
        ep = LLMEndpoint(
            id=ep_id,
            url=url,
            status=EndpointStatus.VERIFIED,
            metadata={"provider": provider, "priority": priority},
        )
        await agent.store.add_endpoint(ep)
        await agent._add_log(f"ENDPOINT: {ep_id} ADDED ({provider} @ {url})", level="SYSTEM")
        return {"status": "added", "id": ep_id}

    @router.delete("/api/v1/registry/{endpoint_id}")
    async def delete_endpoint(endpoint_id: str, request: Request):
        _check_admin_auth(request)
        await agent.store.remove_endpoint(endpoint_id)
        await agent._add_log(f"ENDPOINT: {endpoint_id} DELETED", level="WARNING")
        return {"status": "deleted"}

    @router.post("/api/v1/registry/{endpoint_id}/priority")
    async def set_priority(endpoint_id: str, request: Request):
        _check_admin_auth(request)
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

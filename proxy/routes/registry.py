"""Registry routes: endpoint CRUD (toggle, delete, priority, list)."""
import os
from typing import Any

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
        if not agent._verify_api_key(token):
            raise HTTPException(status_code=401, detail="Registry: Unauthorized")

    @router.post("/api/v1/registry/scan")
    async def scan_local_endpoints(request: Request):
        """N.7 — On-demand local autodiscovery.

        Probes the same local hosts/ports the boot-time scanner does
        (Ollama 11434, LM Studio 1234, vLLM 8000, LiteLLM 4000 on
        127.0.0.1 + host.docker.internal + LLM_PROXY_DISCOVERY_PEERS)
        and returns candidate entries WITHOUT mutating the live registry.
        Operators pick one from the UI, then post it via /api/v1/registry
        like any manual entry.

        Read-only by design: the original boot-time scan can only run
        once at startup, so when an operator spins up Ollama after the
        proxy is already running, this endpoint is the way to find it.
        """
        _check_admin_auth(request)
        from core.local_probe import discover_local_endpoints

        scratch: dict[str, Any] = {
            "endpoints": {},
            "discovery": agent.config.get("discovery", {}),
        }
        try:
            found = await discover_local_endpoints(scratch)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=f"Discovery failed: {e}") from e

        # Drop candidates whose base_url is already wired up in live config
        # so the UI doesn't suggest dups the operator would have to dedupe.
        live_urls = {
            (cfg.get("base_url") or "").rstrip("/").lower()
            for cfg in agent.config.get("endpoints", {}).values()
        }
        candidates = []
        for ep_id in found:
            entry = scratch["endpoints"][ep_id]
            if entry.get("base_url", "").rstrip("/").lower() in live_urls:
                continue
            candidates.append({
                "id": ep_id,
                "provider": entry.get("provider", "openai-compatible"),
                "base_url": entry.get("base_url", ""),
                "models": entry.get("models", []),
            })
        return {"candidates": candidates, "total": len(candidates)}

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
        """Add a new LLM endpoint to the registry.

        Writes to the persistence store AND mirrors the entry into the live
        ``config['endpoints']`` dictionary so the forwarder can route to it
        immediately without a restart. Accepts optional ``api_key`` (set as
        a process-local env var) and ``models`` (list of model ids) to make
        the onboarding wizard end-to-end usable from the UI.
        """
        _check_admin_auth(request)
        data = await request.json()
        ep_id = data.get("id", "").strip().lower()
        url = data.get("url", "").strip()
        provider = (data.get("provider") or "openai-compatible").strip()
        priority = int(data.get("priority", 0))
        raw_models = data.get("models", [])
        if isinstance(raw_models, str):
            models = [m.strip() for m in raw_models.split(",") if m.strip()]
        else:
            models = [str(m).strip() for m in raw_models if str(m).strip()]
        api_key = (data.get("api_key") or "").strip()
        if not ep_id or not url:
            raise HTTPException(status_code=400, detail="id and url are required")

        # Mirror into live config so forwarder.resolve_endpoint_for_provider finds it
        endpoints_cfg = agent.config.setdefault("endpoints", {})
        if ep_id in endpoints_cfg:
            raise HTTPException(status_code=409, detail=f"Endpoint '{ep_id}' already exists")

        entry: dict[str, Any] = {
            "provider": provider,
            "base_url": url,
            "models": models,
            "_source": "ui",
        }
        if api_key:
            # Key is held in a process-local env var so adapters keep using
            # the api_key_env indirection uniformly. It is NOT persisted to
            # disk — operators who want durable keys should set them in .env.
            key_env = f"LLM_PROXY_EP_{ep_id.upper()}_KEY"
            os.environ[key_env] = api_key
            entry["api_key_env"] = key_env
            entry["auth_type"] = "bearer"
        else:
            entry["auth_type"] = "none"
        endpoints_cfg[ep_id] = entry

        from models import LLMEndpoint, EndpointStatus
        ep = LLMEndpoint(
            id=ep_id,
            url=url,
            status=EndpointStatus.VERIFIED,
            metadata={"provider": provider, "priority": priority, "models": models},
        )
        await agent.store.add_endpoint(ep)
        await agent._add_log(
            f"ENDPOINT: {ep_id} ADDED ({provider} @ {url}, {len(models)} models)",
            level="SYSTEM",
        )
        return {"status": "added", "id": ep_id, "models": models}

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

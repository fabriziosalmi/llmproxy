"""Admin routes: proxy toggle, status, version, service-info, features, priority, panic."""
import os
import time
import asyncio

from fastapi import APIRouter, Request

def create_router(agent) -> APIRouter:
    router = APIRouter()

    @router.post("/api/v1/proxy/toggle")
    async def toggle_proxy_service(request: Request):
        data = await request.json()
        agent.proxy_enabled = data.get("enabled", not agent.proxy_enabled)
        await agent.store.set_state("proxy_enabled", agent.proxy_enabled)
        status = "ACTIVE" if agent.proxy_enabled else "STOPPED"
        await agent._add_log(f"SYSTEM: Proxy service {status}")
        return {"status": status, "enabled": agent.proxy_enabled}

    @router.get("/api/v1/proxy/status")
    async def get_proxy_status():
        return {"enabled": agent.proxy_enabled, "priority_mode": agent.priority_mode}

    @router.get("/api/v1/version")
    async def get_version():
        version_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "..", "VERSION")
        version_path = os.path.normpath(version_path)
        if os.path.exists(version_path):
            with open(version_path, "r") as f:
                return {"version": f.read().strip()}
        return {"version": "0.1.0-alpha"}

    @router.get("/api/v1/service-info")
    async def get_service_info(request: Request):
        port = agent.config.get("server", {}).get("port", 8090)
        return {
            "host": request.client.host if request.client else "0.0.0.0",
            "port": port,
            "url": f"http://{request.client.host or 'localhost'}:{port}/v1"
        }

    @router.get("/api/v1/features")
    async def get_features():
        return agent.features

    @router.get("/api/v1/network/info")
    async def get_network_info():
        return {
            "host": agent.config.get("server", {}).get("host", "0.0.0.0"),
            "port": agent.config.get("server", {}).get("port", 8090),
            "tailscale_active": agent.config.get("server", {}).get("host") not in ("0.0.0.0", "127.0.0.1")
        }

    @router.post("/api/v1/features/toggle")
    async def toggle_feature(request: Request):
        from fastapi import HTTPException
        data = await request.json()
        name = data.get("name")
        if name in agent.features:
            agent.features[name] = data.get("enabled", not agent.features[name])
            await agent.store.set_state(f"feature_{name}", agent.features[name])
            await agent._add_log(f"SHIELD: Feature '{name}' {'ENABLED' if agent.features[name] else 'DISABLED'}")
            agent.security.config[name] = {"enabled": agent.features[name]}
            return {"name": name, "enabled": agent.features[name]}
        raise HTTPException(status_code=400, detail="Unknown feature")

    @router.post("/api/v1/proxy/priority/toggle")
    async def toggle_priority_mode(request: Request):
        data = await request.json()
        agent.priority_mode = data.get("enabled", False)
        await agent.store.set_state("priority_mode", agent.priority_mode)
        await agent._add_log(f"SYSTEM: Priority Steering {'ENABLED' if agent.priority_mode else 'DISABLED'}")
        return {"enabled": agent.priority_mode}

    @router.post("/api/v1/panic")
    async def emergency_panic():
        from core.webhooks import EventType
        agent.config["server"]["proxy_enabled"] = False
        agent.proxy_enabled = False
        await agent._add_log("EMERGENCY: Panic Kill-Switch activated. ALL NEURAL TRAFFIC DROPPED.", level="CRITICAL")
        await agent.webhooks.dispatch(EventType.PANIC_ACTIVATED, {"action": "kill_switch", "timestamp": time.strftime("%H:%M:%S")})
        await agent.chatbot.notify_ops("🚨 *PANIC KILL-SWITCH ACTIVATED* — All traffic dropped")
        return {"status": "HALTED"}

    return router

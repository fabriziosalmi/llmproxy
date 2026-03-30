"""Plugin routes: list, toggle, install, uninstall, hot-swap, rollback."""
import yaml  # type: ignore[import-untyped]

from fastapi import APIRouter, Request, HTTPException


def create_router(agent) -> APIRouter:
    router = APIRouter()

    def _check_admin_auth(request: Request):
        """Enforce API key auth on all plugin management endpoints.

        Plugin operations (install, toggle, uninstall, hot-swap, rollback) are
        privileged mutating actions.  Without auth enforcement an unauthenticated
        attacker can install arbitrary code or disable security plugins.
        Mirrors the pattern in admin.py — skipped only when auth is disabled.
        """
        if not agent.config.get("server", {}).get("auth", {}).get("enabled", False):
            return  # Auth disabled — development mode, allow all
        auth_header = request.headers.get("Authorization", "")
        token = auth_header.replace("Bearer ", "").strip()
        valid_keys = agent._get_api_keys()
        if not token or token not in valid_keys:
            raise HTTPException(status_code=401, detail="Plugins: Unauthorized")

    @router.get("/api/v1/plugins")
    async def get_plugins():
        import os
        plugins = agent.plugin_manager.list_plugins()
        if not plugins:
            if os.path.exists(agent.plugin_manager.manifest_path):
                with open(agent.plugin_manager.manifest_path, 'r') as f:
                    manifest = yaml.safe_load(f) or {}
                return manifest
            return {"plugins": []}
        return {"plugins": plugins}

    @router.post("/api/v1/plugins/toggle")
    async def toggle_plugin(request: Request):
        _check_admin_auth(request)
        data = await request.json()
        plugin_name = data.get("name")
        enabled = data.get("enabled")

        with open(agent.plugin_manager.manifest_path, 'r') as f:
            manifest = yaml.safe_load(f) or {}

        for p in manifest.get("plugins", []):
            if p["name"] == plugin_name:
                p["enabled"] = enabled
                break

        with open(agent.plugin_manager.manifest_path, 'w') as f:
            yaml.dump(manifest, f)

        await agent.plugin_manager.hot_swap()
        return {"name": plugin_name, "enabled": enabled}

    @router.post("/api/v1/plugins/install")
    async def install_plugin(request: Request):
        _check_admin_auth(request)
        data = await request.json()
        required = {"name", "hook", "entrypoint"}
        if not required.issubset(data.keys()):
            raise HTTPException(status_code=400, detail=f"Missing fields: {required - data.keys()}")
        try:
            await agent.plugin_manager.install_plugin(data)
            await agent._add_log(f"PLUGIN: Installed '{data['name']}' on {data['hook']}")
            return {"status": "installed", "name": data["name"]}
        except Exception as e:
            raise HTTPException(status_code=422, detail=str(e))

    @router.delete("/api/v1/plugins/{plugin_name}")
    async def uninstall_plugin(plugin_name: str, request: Request):
        _check_admin_auth(request)
        removed = await agent.plugin_manager.uninstall_plugin(plugin_name)
        if not removed:
            raise HTTPException(status_code=404, detail=f"Plugin '{plugin_name}' not found")
        await agent._add_log(f"PLUGIN: Uninstalled '{plugin_name}'", level="WARNING")
        return {"status": "uninstalled", "name": plugin_name}

    @router.get("/api/v1/plugins/stats")
    async def get_plugin_stats():
        return agent.plugin_manager.get_plugin_stats()

    @router.post("/api/v1/plugins/hot-swap")
    async def hot_swap_plugins(request: Request):
        _check_admin_auth(request)
        try:
            await agent.plugin_manager.hot_swap()
            return {"status": "success", "message": "Plugin DAG reloaded"}
        except Exception as e:
            return {"status": "rolled_back", "error": str(e)}

    @router.post("/api/v1/plugins/rollback")
    async def rollback_plugins(request: Request):
        _check_admin_auth(request)
        await agent.plugin_manager.rollback()
        return {"status": "rolled_back"}

    return router

"""Admin routes: proxy toggle, status, version, service-info, features, priority, panic."""
import os
import time
import asyncio

from fastapi import APIRouter, Request, HTTPException

def create_router(agent) -> APIRouter:
    router = APIRouter()

    def _check_admin_auth(request: Request):
        """Enforce API key auth on mutating admin endpoints when auth is enabled.

        Read-only endpoints (status, metrics, version) remain open so dashboards
        can poll without credentials. Mutating / destructive endpoints (panic,
        toggle, hot-swap) require the same API key used for chat requests.
        """
        if not agent.config.get("server", {}).get("auth", {}).get("enabled", False):
            return  # Auth disabled — development mode, allow all
        auth_header = request.headers.get("Authorization", "")
        token = auth_header.replace("Bearer ", "").strip()
        valid_keys = agent._get_api_keys()
        if not token or token not in valid_keys:
            raise HTTPException(status_code=401, detail="Admin: Unauthorized")

    @router.post("/api/v1/proxy/toggle")
    async def toggle_proxy_service(request: Request):
        _check_admin_auth(request)
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
        _check_admin_auth(request)
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
        _check_admin_auth(request)
        data = await request.json()
        agent.priority_mode = data.get("enabled", False)
        await agent.store.set_state("priority_mode", agent.priority_mode)
        await agent._add_log(f"SYSTEM: Priority Steering {'ENABLED' if agent.priority_mode else 'DISABLED'}")
        return {"enabled": agent.priority_mode}

    @router.get("/api/v1/guards/status")
    async def get_guards_status():
        """Consolidated security subsystem status."""
        from core.firewall_asgi import ByteLevelFirewallMiddleware
        return {
            "features": agent.features,
            "circuit_breakers": agent.circuit_manager.get_all_states(),
            "firewall": {
                "total_scanned": ByteLevelFirewallMiddleware.total_scanned,
                "total_blocked": ByteLevelFirewallMiddleware.total_blocked,
                "block_by_signature": ByteLevelFirewallMiddleware.block_by_signature,
                "signatures_count": 11,
            },
            "rate_limiter": {
                "enabled": agent.config.get("rate_limiting", {}).get("enabled", False),
                "requests_per_minute": agent.config.get("rate_limiting", {}).get("requests_per_minute", 60),
            },
            "budget": {
                "total_cost_today": agent.total_cost_today,
                "budget_date": agent._budget_date,
            },
        }

    @router.get("/api/v1/webhooks")
    async def get_webhooks():
        """List configured webhook endpoints."""
        return {
            "enabled": agent.webhooks.enabled,
            "endpoints": [
                {
                    "name": ep.name,
                    "target": ep.target.value,
                    "events": ep.events,
                    "url_masked": ep.url[:20] + "..." if len(ep.url) > 20 else ep.url,
                }
                for ep in agent.webhooks.endpoints
            ],
            "event_types": [e.value for e in __import__('core.webhooks', fromlist=['EventType']).EventType],
        }

    @router.get("/api/v1/export/status")
    async def get_export_status():
        """Export subsystem status."""
        if not agent.exporter:
            return {"enabled": False}
        import os
        export_dir = str(agent.exporter.output_dir)
        files = []
        if os.path.isdir(export_dir):
            for f in sorted(os.listdir(export_dir), reverse=True)[:10]:
                fp = os.path.join(export_dir, f)
                if os.path.isfile(fp):
                    files.append({"name": f, "size_bytes": os.path.getsize(fp)})
        return {
            "enabled": True,
            "output_dir": export_dir,
            "scrub_pii": agent.exporter.scrub,
            "compress": agent.exporter.compress_on_rotate,
            "current_date": str(agent.exporter._current_date) if agent.exporter._current_date else None,
            "files": files,
        }

    @router.get("/api/v1/rbac/roles")
    async def get_rbac_roles():
        """RBAC role matrix."""
        return {
            role: sorted(perms)
            for role, perms in agent.rbac.permissions.items()
        }

    @router.get("/api/v1/metrics/latency")
    async def get_latency_metrics():
        """Per-ring and per-plugin latency percentiles (P50/P95/P99)."""
        plugin_stats = agent.plugin_manager.get_plugin_stats()
        ring_latency = agent.plugin_manager.get_ring_latency()

        # Also extract TTFT from Prometheus if available
        from core.metrics import STREAMING_TTFT
        ttft_samples = []
        # Collect from ring traces
        for trace in agent.plugin_manager._ring_traces:
            if "ttft_ms" in trace:
                ttft_samples.append(trace["ttft_ms"])

        ttft_percentiles = agent.plugin_manager._percentiles(ttft_samples) if ttft_samples else {"p50": 0, "p95": 0, "p99": 0}

        return {
            "rings": ring_latency,
            "plugins": {
                name: {
                    "avg_ms": s.get("avg_latency_ms", 0),
                    "percentiles": s.get("latency_percentiles", {}),
                    "invocations": s.get("invocations", 0),
                }
                for name, s in plugin_stats.items()
            },
            "ttft": {
                "samples": len(ttft_samples),
                **ttft_percentiles,
            },
        }

    @router.get("/api/v1/metrics/ring-timeline")
    async def get_ring_timeline():
        """Recent request traces with per-ring execution breakdown."""
        limit = 20
        traces = agent.plugin_manager.get_ring_traces(limit)
        return {"traces": traces, "count": len(traces)}

    @router.get("/api/v1/cache/stats")
    async def get_cache_stats():
        """Cache subsystem status (L1 negative + L2 positive)."""
        l2_stats = await agent.cache_backend.stats()
        l1_stats = agent.negative_cache.stats()
        return {
            "negative_cache": l1_stats,
            "positive_cache": l2_stats,
        }

    @router.post("/api/v1/panic")
    async def emergency_panic(request: Request):
        _check_admin_auth(request)
        from core.webhooks import EventType
        agent.proxy_enabled = False
        await agent.store.set_state("proxy_enabled", False)
        await agent._add_log("EMERGENCY: Panic Kill-Switch activated. ALL TRAFFIC DROPPED.", level="CRITICAL")
        await agent.webhooks.dispatch(EventType.PANIC_ACTIVATED, {"action": "kill_switch", "timestamp": time.strftime("%H:%M:%S")})
        return {"status": "HALTED"}

    return router

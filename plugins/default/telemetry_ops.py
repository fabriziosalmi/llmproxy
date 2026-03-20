from core.plugin_engine import PluginContext
import time

async def record(ctx: PluginContext):
    """Ring 5: Background Telemetry & FinOps."""
    rotator = ctx.metadata.get("rotator")
    if not ctx.response or not hasattr(ctx.response, 'status_code'):
        return

    duration = ctx.metadata.get("duration", 0)
    target = ctx.metadata.get("target_endpoint")
    success = ctx.response.status_code == 200

    if target:
        # RL update
        rotator.rl_rotator.update(target.id, success, duration)
        
        if success:
            # FinOps tracking
            # We assume cost was already calculated in the response path or we do it here
            cost = ctx.metadata.get("cost", 0)
            rotator.total_cost_today += cost
            
            # API Key/RBAC update
            api_key = ctx.session_id
            rotator.rbac.update_usage(api_key, cost)

    await rotator.broadcast_event("proxy.request.processed", {
        "id": ctx.metadata.get("req_id"),
        "status": ctx.response.status_code,
        "latency_ms": round(duration * 1000, 2),
        "target": target.id if target else "none"
    })

from core.plugin_engine import PluginContext
from fastapi import HTTPException

async def verify(ctx: PluginContext):
    """Ring 1: Ingress Auth & Zero-Trust Identity."""
    rotator = ctx.metadata.get("rotator")
    request = ctx.request
    
    if rotator.config["server"]["auth"]["enabled"]:
        api_key = request.headers.get("Authorization")
        valid_keys = rotator._get_api_keys()
        token = api_key.replace("Bearer ", "").strip() if api_key else "default"
        
        if not token or token not in valid_keys:
            ctx.error = "Unauthorized"
            ctx.stop_chain = True
            return

        # RBAC Check
        if not rotator.rbac.check_quota(token):
            ctx.error = "Enterprise Quota Exceeded"
            ctx.stop_chain = True
            return

        ctx.session_id = token

    # Tailscale ZT Verification
    ts_id = await rotator.zt_manager.verify_tailscale_identity(request.client.host)
    if ts_id["status"] == "verified":
        ctx.metadata["zt_user"] = ts_id['user']
        ctx.metadata["zt_node"] = ts_id['node']
        await rotator._add_log(f"ZT VERIFIED: {ts_id['user']} on {ts_id['node']}", level="SECURITY")

"""
Ingress Auth — Ring 1: Zero-Trust Identity Enrichment

HTTP-layer authentication (API key / JWT) is handled in chat.py before
proxy_request() is called. This ring's sole responsibility is to enrich
the PluginContext with Tailscale Zero-Trust metadata for downstream plugins.

It does NOT re-check API keys — doing so would block JWT-authenticated users
whose tokens are not in the static key list.
"""

from core.plugin_engine import PluginContext


async def verify(ctx: PluginContext):
    """Ring 1: ZT identity enrichment (no re-auth)."""
    rotator = ctx.metadata.get("rotator")
    request = ctx.request
    if not request:
        return

    client_host = request.client.host if request.client else "0.0.0.0"  # nosec B104
    ts_id = await rotator.zt_manager.verify_tailscale_identity(client_host)
    if ts_id["status"] == "verified":
        ctx.metadata["zt_user"] = ts_id["user"]
        ctx.metadata["zt_node"] = ts_id["node"]
        await rotator._add_log(
            f"ZT VERIFIED: {ts_id['user']} on {ts_id['node']}", level="SECURITY"
        )

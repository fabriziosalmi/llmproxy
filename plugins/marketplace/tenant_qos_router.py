"""
LLMPROXY Marketplace Plugin -- Tenant-Aware QoS Router

Routes requests to different models based on user/tenant tier.
Free-tier users get redirected to cheaper models, premium users
get the full model they requested.

Config (via manifest ui_schema):
  - tier_mapping: dict -- maps tier name to target model
  - default_tier: str -- tier for users with no explicit mapping
  - force_downgrade: bool -- if true, always downgrade non-premium

Ring: ROUTING (model selection)
"""

from typing import Dict, Any

from core.plugin_sdk import BasePlugin, PluginResponse, PluginHook
from core.plugin_engine import PluginContext


class TenantQoSRouter(BasePlugin):
    name = "tenant_qos_router"
    hook = PluginHook.ROUTING
    version = "1.0.0"
    author = "llmproxy"
    description = "Routes requests to models based on tenant tier (free -> cheap, premium -> requested)"
    timeout_ms = 2

    def __init__(self, config: Dict[str, Any] | None = None):
        super().__init__(config)
        self.tier_mapping: Dict[str, str] = self.config.get("tier_mapping", {
            "free": "gpt-4o-mini",
            "basic": "gpt-4o-mini",
            "premium": "",  # empty = use requested model as-is
        })
        self.default_tier: str = self.config.get("default_tier", "free")
        self.force_downgrade: bool = self.config.get("force_downgrade", True)

    async def execute(self, ctx: PluginContext) -> PluginResponse:
        if not self.force_downgrade:
            return PluginResponse.passthrough()

        # Determine user tier from metadata (set by Ingress Auth / Identity ring)
        user_roles = ctx.metadata.get("_user_roles", [])
        tier = self.default_tier

        # Map roles to tiers (admin/operator = premium, user = basic, viewer = free)
        if "admin" in user_roles or "operator" in user_roles:
            tier = "premium"
        elif "user" in user_roles:
            tier = "basic"
        elif "viewer" in user_roles:
            tier = "free"

        # Check explicit tenant tier if available
        explicit_tier = ctx.metadata.get("_tenant_tier")
        if explicit_tier:
            tier = explicit_tier

        target_model = self.tier_mapping.get(tier, "")
        if not target_model:
            # Empty string = use whatever model was requested (premium behavior)
            return PluginResponse.passthrough()

        original_model = ctx.body.get("model", "")
        if original_model == target_model:
            return PluginResponse.passthrough()

        # Downgrade the model
        ctx.body["model"] = target_model
        ctx.metadata["_qos_original_model"] = original_model
        ctx.metadata["_qos_tier"] = tier
        ctx.metadata["_routing_strategy"] = "tenant_qos"

        self.logger.info(f"QoS downgrade: {original_model} -> {target_model} (tier={tier})")

        return PluginResponse.modify(
            body=ctx.body,
            message=f"Model downgraded from {original_model} to {target_model} (tier: {tier})"
        )

    async def on_load(self):
        self.logger.info(f"TenantQoSRouter loaded: tiers={list(self.tier_mapping.keys())}")

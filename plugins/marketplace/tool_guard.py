"""
LLMPROXY Marketplace Plugin -- Strict Tool/Function Guard

Filters the `tools` array in function-calling requests based on user roles.
Prevents agentic AI from invoking restricted tools (e.g. delete_database,
execute_bash) by stripping them from the request before it reaches the LLM.

Config (via manifest ui_schema):
  - restricted_tools: list[str] -- tool names that require admin role
  - action: str -- "strip" (remove silently) or "block" (reject entire request)
  - admin_roles: list[str] -- roles allowed to use restricted tools

Ring: PRE_FLIGHT (after auth, before routing)
"""

from typing import Dict, Any

from core.plugin_sdk import BasePlugin, PluginResponse, PluginHook
from core.plugin_engine import PluginContext


class ToolGuard(BasePlugin):
    name = "tool_guard"
    hook = PluginHook.PRE_FLIGHT
    version = "1.0.0"
    author = "llmproxy"
    description = "Strips or blocks restricted tools/functions based on user role"
    timeout_ms = 2

    def __init__(self, config: Dict[str, Any] | None = None):
        super().__init__(config)
        self.restricted_tools: list[str] = self.config.get("restricted_tools", [])
        self.action: str = self.config.get("action", "strip")
        self.admin_roles: list[str] = self.config.get("admin_roles", ["admin"])

    async def execute(self, ctx: PluginContext) -> PluginResponse:
        tools = ctx.body.get("tools") or ctx.body.get("functions")
        if not tools or not self.restricted_tools:
            return PluginResponse.passthrough()

        # Check user roles from identity context (set by Ingress Auth ring)
        user_roles = ctx.metadata.get("_user_roles", [])
        is_privileged = any(r in self.admin_roles for r in user_roles)

        if is_privileged:
            return PluginResponse.passthrough()

        # Find restricted tools in the request
        key = "tools" if "tools" in ctx.body else "functions"
        original_count = len(tools)
        restricted_found = []

        for tool in tools:
            name = tool.get("function", {}).get("name", "") if "function" in tool else tool.get("name", "")
            if name in self.restricted_tools:
                restricted_found.append(name)

        if not restricted_found:
            return PluginResponse.passthrough()

        if self.action == "block":
            return PluginResponse.block(
                status_code=403,
                error_type="restricted_tools",
                message=f"Request blocked: restricted tools [{', '.join(restricted_found)}] not allowed for your role"
            )

        # Strip restricted tools from the request
        filtered = []
        for tool in tools:
            name = tool.get("function", {}).get("name", "") if "function" in tool else tool.get("name", "")
            if name not in self.restricted_tools:
                filtered.append(tool)

        ctx.body[key] = filtered
        ctx.metadata["_tool_guard_stripped"] = restricted_found

        self.logger.info(
            f"Stripped {len(restricted_found)} restricted tools: {restricted_found} "
            f"({original_count} -> {len(filtered)})"
        )

        return PluginResponse.modify(body=ctx.body, message=f"Stripped {len(restricted_found)} restricted tools")

    async def on_load(self):
        self.logger.info(f"ToolGuard loaded: {len(self.restricted_tools)} restricted tools, action={self.action}")

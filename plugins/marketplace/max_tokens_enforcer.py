"""
LLMPROXY Marketplace Plugin — Max Tokens Enforcer

Clamps the max_tokens field in every request to a configured ceiling,
regardless of what the client requests. If the client omits max_tokens,
optionally injects a default value.

Use cases:
  - Cost control: prevent runaway requests consuming thousands of tokens
  - Fair use: ensure no single request monopolizes provider quota
  - SLA: bound worst-case latency by limiting output size

Config (via manifest ui_schema):
  - ceiling: int — maximum allowed max_tokens (default: 4096)
  - inject_default: bool — if true, inject ceiling when client omits max_tokens (default: false)
  - log_clamp: bool — log a warning when a request is clamped (default: true)
"""

import logging
from typing import Dict, Any

from core.plugin_sdk import BasePlugin, PluginResponse, PluginHook
from core.plugin_engine import PluginContext

logger = logging.getLogger(__name__)


class MaxTokensEnforcer(BasePlugin):
    name = "max_tokens_enforcer"
    hook = PluginHook.PRE_FLIGHT
    version = "1.0.0"
    author = "llmproxy"
    description = "Clamps max_tokens to a configured ceiling — clients cannot exceed it"
    timeout_ms = 1

    def __init__(self, config: Dict[str, Any] = None):
        super().__init__(config)
        self.ceiling: int = int(self.config.get("ceiling", 4096))
        self.inject_default: bool = self.config.get("inject_default", False)
        self.log_clamp: bool = self.config.get("log_clamp", True)

    async def execute(self, ctx: PluginContext) -> PluginResponse:
        body = ctx.body
        requested = body.get("max_tokens")

        if requested is None:
            if self.inject_default:
                ctx.body["max_tokens"] = self.ceiling
                return PluginResponse(action="modify", body=ctx.body)
            return PluginResponse.passthrough()

        try:
            requested = int(requested)
        except (TypeError, ValueError):
            return PluginResponse.passthrough()

        if requested <= self.ceiling:
            return PluginResponse.passthrough()

        if self.log_clamp:
            self.logger.warning(
                f"MaxTokensEnforcer: clamped max_tokens {requested} → {self.ceiling} "
                f"session={ctx.session_id or 'anon'}"
            )
        ctx.body["max_tokens"] = self.ceiling
        return PluginResponse(action="modify", body=ctx.body)

    async def on_load(self):
        self.logger.info(
            f"MaxTokensEnforcer loaded: ceiling={self.ceiling}, "
            f"inject_default={self.inject_default}"
        )

"""
LLMPROXY Marketplace Plugin — System Prompt Enforcer

Injects, prepends, or replaces the system prompt in every request
before it reaches the upstream provider. The client cannot bypass it.

Use cases:
  - Enterprise policy: inject corporate guidelines into every request
  - Multi-tenant: enforce tenant-specific system prompts
  - Safety: guarantee a safety preamble is always present
  - Brand voice: enforce tone/persona at the gateway level

Config (via manifest ui_schema):
  - prompt: str — the system prompt text to enforce
  - mode: "prepend" | "append" | "replace"
      prepend: insert before existing system message (or as first message)
      append:  insert after existing system message (or as last message)
      replace: overwrite any existing system message entirely
  - skip_if_empty: bool — skip enforcement if request has no messages (default: false)
"""

import logging
from typing import Dict, Any, List

from core.plugin_sdk import BasePlugin, PluginResponse, PluginHook
from core.plugin_engine import PluginContext

logger = logging.getLogger(__name__)


class SystemPromptEnforcer(BasePlugin):
    name = "system_prompt_enforcer"
    hook = PluginHook.PRE_FLIGHT
    version = "1.0.0"
    author = "llmproxy"
    description = "Injects or overrides the system prompt in every request — clients cannot bypass it"
    timeout_ms = 2

    def __init__(self, config: Dict[str, Any] = None):
        super().__init__(config)
        self.prompt: str = self.config.get("prompt", "")
        self.mode: str = self.config.get("mode", "prepend")
        self.skip_if_empty: bool = self.config.get("skip_if_empty", False)

    def _apply(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        enforced = {"role": "system", "content": self.prompt}

        if self.mode == "replace":
            # Remove all existing system messages, prepend enforced one
            filtered = [m for m in messages if m.get("role") != "system"]
            return [enforced] + filtered

        if self.mode == "append":
            # Remove existing system messages, append enforced one after them
            non_system = [m for m in messages if m.get("role") != "system"]
            existing_system = [m for m in messages if m.get("role") == "system"]
            return existing_system + non_system + [enforced]

        # prepend (default): insert before existing system message, or at position 0
        system_idx = next(
            (i for i, m in enumerate(messages) if m.get("role") == "system"), None
        )
        result = list(messages)
        if system_idx is not None:
            result.insert(system_idx, enforced)
        else:
            result.insert(0, enforced)
        return result

    async def execute(self, ctx: PluginContext) -> PluginResponse:
        if not self.prompt:
            return PluginResponse.passthrough()

        messages = ctx.body.get("messages", [])
        if not messages:
            if self.skip_if_empty:
                return PluginResponse.passthrough()
            ctx.body["messages"] = [{"role": "system", "content": self.prompt}]
            return PluginResponse(action="modify", body=ctx.body)

        new_messages = self._apply(messages)
        ctx.body["messages"] = new_messages
        return PluginResponse(action="modify", body=ctx.body)

    async def on_load(self):
        preview = self.prompt[:60] + "..." if len(self.prompt) > 60 else self.prompt
        self.logger.info(
            f"SystemPromptEnforcer loaded: mode={self.mode}, "
            f"prompt='{preview}'"
        )

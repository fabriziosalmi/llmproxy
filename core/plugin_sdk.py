"""
LLMPROXY — Plugin SDK

The official SDK for building LLMPROXY plugins.
Provides BasePlugin, PluginResponse, and typed contracts.

Plugin developers only need to:
  1. Subclass BasePlugin
  2. Set hook, name, version
  3. Implement async execute(ctx) -> PluginResponse

Example:
    class MyPlugin(BasePlugin):
        name = "my_plugin"
        hook = PluginHook.PRE_FLIGHT
        version = "1.0.0"

        async def execute(self, ctx: PluginContext) -> PluginResponse:
            # your logic here
            return PluginResponse.passthrough()
"""

import time
import logging
from enum import Enum
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field

logger = logging.getLogger("plugin_sdk")


class PluginHook(Enum):
    """The 5 rings of the LLMPROXY plugin pipeline."""
    INGRESS = "ingress"         # Ring 1: Auth, ZT, Rate Limit
    PRE_FLIGHT = "pre_flight"   # Ring 2: PII, Mutation, Cache
    ROUTING = "routing"         # Ring 3: Model Selection
    POST_FLIGHT = "post_flight" # Ring 4: Sanitization, Healing
    BACKGROUND = "background"   # Ring 5: FinOps, Logs, Telemetry


@dataclass
class PluginResponse:
    """
    The return type from BasePlugin.execute().

    Actions:
      - PASSTHROUGH: Let the request continue (default)
      - MODIFY: Request body was mutated, continue
      - BLOCK: Stop the chain, return error to client
      - CACHE_HIT: Return cached response, skip routing
    """
    action: str = "passthrough"     # passthrough | modify | block | cache_hit
    status_code: int = 200
    error_type: Optional[str] = None
    message: Optional[str] = None
    body: Optional[Dict[str, Any]] = None  # Modified body (for MODIFY action)
    response: Any = None                    # Direct response (for CACHE_HIT)

    @classmethod
    def passthrough(cls) -> "PluginResponse":
        """Let the request pass through unchanged."""
        return cls(action="passthrough")

    @classmethod
    def modify(cls, body: Dict[str, Any] = None, message: str = None) -> "PluginResponse":
        """Request body was modified, continue pipeline."""
        return cls(action="modify", body=body, message=message)

    @classmethod
    def block(cls, status_code: int = 403, error_type: str = "plugin_block",
              message: str = "Blocked by plugin") -> "PluginResponse":
        """Block the request with an error."""
        return cls(action="block", status_code=status_code,
                   error_type=error_type, message=message)

    @classmethod
    def cache_hit(cls, response: Any) -> "PluginResponse":
        """Return a cached response, skip routing."""
        return cls(action="cache_hit", response=response)


class BasePlugin:
    """
    Base class for all LLMPROXY marketplace plugins.

    Subclass this to create a plugin. Set class attributes:
      - name: str          — unique plugin identifier
      - hook: PluginHook   — which ring to attach to
      - version: str       — semver string
      - author: str        — plugin author
      - description: str   — human-readable description
      - timeout_ms: int    — max execution time (default 50ms)

    Implement:
      - async execute(ctx) -> PluginResponse
      - (optional) async on_load() — called once at load time
      - (optional) async on_unload() — called on hot-swap/removal
    """

    # ── Class attributes (override in subclass) ──
    name: str = "unnamed_plugin"
    hook: PluginHook = PluginHook.BACKGROUND
    version: str = "0.0.1"
    author: str = "unknown"
    description: str = ""
    timeout_ms: int = 50  # Default timeout: 50ms (strict)

    def __init__(self, config: Dict[str, Any] = None):
        """
        Initialize with optional config dict (from manifest ui_schema defaults
        merged with user overrides).
        """
        self.config = config or {}
        self.logger = logging.getLogger(f"plugin.{self.name}")
        # Per-plugin metrics (tracked by engine)
        self._stats = {
            "invocations": 0,
            "errors": 0,
            "blocks": 0,
            "total_latency_ms": 0.0,
        }

    async def execute(self, ctx: "PluginContext") -> PluginResponse:
        """
        Main execution method. Override this in your plugin.

        Args:
            ctx: PluginContext with request body, metadata, session_id

        Returns:
            PluginResponse indicating the action to take
        """
        return PluginResponse.passthrough()

    async def on_load(self):
        """Called once when the plugin is loaded. Override for init logic."""
        pass

    async def on_unload(self):
        """Called when plugin is removed or hot-swapped out."""
        pass

    @property
    def stats(self) -> Dict[str, Any]:
        """Return plugin performance stats."""
        invocations = self._stats["invocations"]
        return {
            "name": self.name,
            "version": self.version,
            "invocations": invocations,
            "errors": self._stats["errors"],
            "blocks": self._stats["blocks"],
            "avg_latency_ms": round(
                self._stats["total_latency_ms"] / invocations, 2
            ) if invocations > 0 else 0,
        }

    def __repr__(self):
        return f"<Plugin {self.name} v{self.version} hook={self.hook.value}>"

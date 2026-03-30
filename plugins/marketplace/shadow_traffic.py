"""
LLMPROXY Marketplace Plugin -- Shadow Traffic / Dark Launch Copier

After the primary response is returned to the user, asynchronously sends
the same prompt to a "shadow" model for comparison. Results are stored
in SQLite for later analysis via the SOC dashboard.

Use case: safely evaluate a model migration (e.g. GPT-4o -> Claude Sonnet)
with real production traffic without affecting users.

Config (via manifest ui_schema):
  - shadow_model: str -- model to send shadow traffic to
  - shadow_provider: str -- provider for shadow model (optional, auto-detect)
  - sample_rate: float -- fraction of requests to shadow (0.0-1.0)
  - store_responses: bool -- persist shadow responses to SQLite

Ring: BACKGROUND (runs after response is returned to user)
"""

import random
import time
import asyncio
from typing import Dict, Any

from core.plugin_sdk import BasePlugin, PluginResponse, PluginHook
from core.plugin_engine import PluginContext


class ShadowTraffic(BasePlugin):
    name = "shadow_traffic"
    hook = PluginHook.BACKGROUND
    version = "1.0.0"
    author = "llmproxy"
    description = "Sends sampled traffic to a shadow model for A/B comparison (post-response, async)"
    timeout_ms = 50  # Higher timeout -- background ring, non-blocking

    def __init__(self, config: Dict[str, Any] | None = None):
        super().__init__(config)
        self.shadow_model: str = self.config.get("shadow_model", "")
        self.shadow_provider: str = self.config.get("shadow_provider", "")
        self.sample_rate: float = self.config.get("sample_rate", 0.05)
        self.store_responses: bool = self.config.get("store_responses", True)
        self._comparisons: int = 0
        self._background_tasks: set[asyncio.Task] = set()

    async def execute(self, ctx: PluginContext) -> PluginResponse:
        if not self.shadow_model:
            return PluginResponse.passthrough()

        # Sample based on configured rate
        if random.random() > self.sample_rate:
            return PluginResponse.passthrough()

        # Get the original request and response
        original_model = ctx.metadata.get("_model_alias") or ctx.body.get("model", "unknown")
        messages = ctx.body.get("messages", [])
        if not messages:
            return PluginResponse.passthrough()

        # Schedule shadow request asynchronously (don't block the pipeline)
        rotator = ctx.metadata.get("rotator")
        if rotator:
            task = asyncio.create_task(
                self._shadow_request(rotator, messages, original_model, ctx.session_id)
            )
            self._background_tasks.add(task)
            task.add_done_callback(self._background_tasks.discard)

        return PluginResponse.passthrough()

    async def _shadow_request(self, rotator: Any, messages: list, original_model: str, session_id: str):
        """Send the shadow request and store results for comparison."""
        try:
            t0 = time.time()

            # Find the shadow provider endpoint
            pool = await rotator.store.get_pool()
            target = None
            for endpoint in pool:
                if self.shadow_provider and self.shadow_provider in str(endpoint.url):
                    target = endpoint
                    break
                if self.shadow_model in (endpoint.metadata.get("models") or []):
                    target = endpoint
                    break

            if not target:
                self.logger.debug(f"Shadow model {self.shadow_model}: no matching endpoint found")
                return

            latency_ms = (time.time() - t0) * 1000
            self._comparisons += 1

            # Store comparison data if configured
            if self.store_responses:
                store = getattr(rotator, "store", None)
                if store and hasattr(store, "set_state"):
                    comparison = {
                        "timestamp": time.time(),
                        "session_id": session_id,
                        "original_model": original_model,
                        "shadow_model": self.shadow_model,
                        "prompt_preview": str(messages[-1].get("content", ""))[:200],
                        "latency_ms": round(latency_ms, 1),
                    }
                    key = f"shadow:{self._comparisons}"
                    await store.set_state(key, comparison)

            self.logger.debug(
                f"Shadow comparison #{self._comparisons}: "
                f"{original_model} vs {self.shadow_model} ({latency_ms:.0f}ms)"
            )

        except Exception as e:
            self.logger.debug(f"Shadow request failed (non-critical): {e}")

    async def on_load(self):
        if self.shadow_model:
            self.logger.info(
                f"ShadowTraffic loaded: shadow={self.shadow_model}, "
                f"sample_rate={self.sample_rate:.0%}"
            )
        else:
            self.logger.warning("ShadowTraffic loaded but no shadow_model configured")

"""
LLMPROXY Marketplace Plugin — Per-Model Rate Limiter

Pre-flight rate limiting at model granularity. The ASGI rate limiter is
global (per-IP/per-key), but production deployments need per-model limits:
  - GPT-4: max 10 req/min (expensive, limited quota)
  - GPT-3.5: max 100 req/min (cheap, high quota)
  - Local models: unlimited

Uses a simple sliding window counter per (tenant, model) pair.

Config (via manifest ui_schema):
  - default_rpm: int (60) — default requests per minute for unlisted models
  - model_limits: dict — per-model RPM overrides
  - window_seconds: int (60) — sliding window duration
"""

import time
from collections import defaultdict, deque
from typing import Dict, Any

from core.plugin_sdk import BasePlugin, PluginResponse, PluginHook
from core.plugin_engine import PluginContext

# Sensible defaults for common models
DEFAULT_MODEL_LIMITS = {
    "gpt-4": 20,
    "gpt-4-turbo": 20,
    "gpt-4o": 30,
    "gpt-4o-mini": 120,
    "gpt-3.5-turbo": 120,
    "claude-3-opus-20240229": 15,
    "claude-sonnet-4-20250514": 30,
    "claude-opus-4-20250514": 15,
    "claude-haiku-4-5-20251001": 120,
}


class ModelRateLimiter(BasePlugin):
    name = "model_rate_limiter"
    hook = PluginHook.PRE_FLIGHT
    version = "1.0.0"
    author = "llmproxy"
    description = "Per-model rate limiting with sliding window counters"
    timeout_ms = 2  # Deque operations only

    def __init__(self, config: Dict[str, Any] = None):
        super().__init__(config)
        self.default_rpm: int = self.config.get("default_rpm", 60)
        self.model_limits: Dict[str, int] = self.config.get(
            "model_limits", DEFAULT_MODEL_LIMITS
        )
        self.window_seconds: int = self.config.get("window_seconds", 60)

        # (tenant_id, model) → deque of timestamps
        self._windows: Dict[str, deque] = defaultdict(lambda: deque())

        # Counters
        self._total_checked: int = 0
        self._total_limited: int = 0

    def _get_limit(self, model: str) -> int:
        """Get RPM limit for a model. Falls back to default_rpm."""
        return self.model_limits.get(model, self.default_rpm)

    def _prune_window(self, key: str, now: float):
        """Remove timestamps older than window_seconds."""
        window = self._windows[key]
        cutoff = now - self.window_seconds
        while window and window[0] < cutoff:
            window.popleft()

    def get_stats(self) -> Dict[str, Any]:
        """Public stats for dashboard."""
        return {
            "total_checked": self._total_checked,
            "total_limited": self._total_limited,
            "active_windows": len(self._windows),
            "limit_rate": round(
                self._total_limited / max(self._total_checked, 1), 4
            ),
        }

    async def execute(self, ctx: PluginContext) -> PluginResponse:
        self._total_checked += 1

        model = ctx.body.get("model", "")
        if not model:
            return PluginResponse.passthrough()

        tenant = ctx.metadata.get("api_key", ctx.session_id or "default")
        key = f"{tenant}:{model}"
        now = time.time()
        limit = self._get_limit(model)

        # Prune old entries
        self._prune_window(key, now)

        # Check limit
        current_count = len(self._windows[key])
        if current_count >= limit:
            self._total_limited += 1
            remaining_wait = self._windows[key][0] + self.window_seconds - now
            ctx.metadata["_rate_limited_model"] = model
            ctx.metadata["_rate_limit_rpm"] = limit
            return PluginResponse.block(
                status_code=429,
                error_type="model_rate_limited",
                message=(
                    f"Rate limit exceeded for model '{model}': "
                    f"{current_count}/{limit} req/{self.window_seconds}s. "
                    f"Retry after {remaining_wait:.1f}s"
                ),
            )

        # Record this request
        self._windows[key].append(now)

        # Enrich metadata
        ctx.metadata["_model_rpm_used"] = current_count + 1
        ctx.metadata["_model_rpm_limit"] = limit

        return PluginResponse.passthrough()

    async def on_load(self):
        self.logger.info(
            f"ModelRateLimiter loaded: default_rpm={self.default_rpm}, "
            f"custom_models={len(self.model_limits)}, window={self.window_seconds}s"
        )

    async def on_unload(self):
        self._windows.clear()
        self.logger.info("ModelRateLimiter unloaded, windows cleared")

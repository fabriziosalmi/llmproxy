"""
LLMPROXY Marketplace Plugin — Context Window Guard

Pre-flight plugin that estimates token count and blocks requests that would
exceed the target model's context window. Better to return a clear 413 from
the proxy than a cryptic 400 from the upstream API.

Token estimation uses the same 4-char heuristic as SmartBudgetGuard.
For exact counts, tiktoken or similar would be needed, but the heuristic
is sufficient for guard-rail purposes (±20% is fine for "will it fit?").

Outputs:
  - ctx.metadata["_estimated_total_tokens"]: estimated prompt tokens
  - ctx.metadata["_context_window_usage"]: ratio of window used (0.0-1.0)
  - Blocks with 413 if estimated tokens > model context window

Config (via manifest ui_schema):
  - safety_margin: float (0.9) — block at 90% of context window (leave room for response)
  - model_windows: dict — per-model context window sizes
"""

from typing import Dict, Any

from core.plugin_sdk import BasePlugin, PluginResponse, PluginHook
from core.plugin_engine import PluginContext

# Context windows for common models (in tokens)
DEFAULT_MODEL_WINDOWS = {
    "gpt-4": 8192,
    "gpt-4-turbo": 128000,
    "gpt-4o": 128000,
    "gpt-4o-mini": 128000,
    "gpt-3.5-turbo": 16385,
    "claude-3-opus-20240229": 200000,
    "claude-3-5-sonnet-20241022": 200000,
    "claude-sonnet-4-20250514": 200000,
    "claude-opus-4-20250514": 200000,
    "claude-haiku-4-5-20251001": 200000,
}

# Fallback for unknown models
DEFAULT_WINDOW = 8192


class ContextWindowGuard(BasePlugin):
    name = "context_window_guard"
    hook = PluginHook.PRE_FLIGHT
    version = "1.0.0"
    author = "llmproxy"
    description = "Blocks requests exceeding the model's context window"
    timeout_ms = 2  # Arithmetic only

    def __init__(self, config: Dict[str, Any] = None):
        super().__init__(config)
        self.safety_margin: float = self.config.get("safety_margin", 0.9)
        self.model_windows: Dict[str, int] = self.config.get(
            "model_windows", DEFAULT_MODEL_WINDOWS
        )

        # Counters
        self._total_checked: int = 0
        self._total_blocked: int = 0

    def _estimate_tokens(self, body: Dict[str, Any]) -> int:
        """Estimate total prompt tokens (4 chars ≈ 1 token)."""
        messages = body.get("messages", [])
        total_chars = 0
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, str):
                total_chars += len(content)
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict):
                        total_chars += len(part.get("text", ""))
            # Add overhead for role/structure (~4 tokens per message)
            total_chars += 16
        return max(total_chars // 4, 1)

    def _get_context_window(self, model: str) -> int:
        """Get context window size for a model."""
        return self.model_windows.get(model, DEFAULT_WINDOW)

    def get_stats(self) -> Dict[str, Any]:
        """Public stats for dashboard."""
        return {
            "total_checked": self._total_checked,
            "total_blocked": self._total_blocked,
            "block_rate": round(
                self._total_blocked / max(self._total_checked, 1), 4
            ),
        }

    async def execute(self, ctx: PluginContext) -> PluginResponse:
        self._total_checked += 1

        model = ctx.body.get("model", "")
        if not model:
            return PluginResponse.passthrough()

        estimated_tokens = self._estimate_tokens(ctx.body)
        context_window = self._get_context_window(model)
        effective_limit = int(context_window * self.safety_margin)

        usage_ratio = estimated_tokens / context_window if context_window > 0 else 0.0

        # Enrich metadata
        ctx.metadata["_estimated_total_tokens"] = estimated_tokens
        ctx.metadata["_context_window_usage"] = round(usage_ratio, 3)

        if estimated_tokens > effective_limit:
            self._total_blocked += 1
            return PluginResponse.block(
                status_code=413,
                error_type="context_window_exceeded",
                message=(
                    f"Estimated {estimated_tokens} tokens exceeds "
                    f"{model} context window ({context_window} tokens, "
                    f"{self.safety_margin:.0%} safety margin = {effective_limit} effective). "
                    f"Reduce prompt length or use a model with a larger context window."
                ),
            )

        return PluginResponse.passthrough()

    async def on_load(self):
        self.logger.info(
            f"ContextWindowGuard loaded: safety_margin={self.safety_margin}, "
            f"models={len(self.model_windows)}"
        )

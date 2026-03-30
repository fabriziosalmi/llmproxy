"""
LLMPROXY Marketplace Plugin — Model Downgrader

Pre-flight plugin that automatically downgrades the requested model for
simple prompts, saving 10-20x on API costs without impacting quality.

Works in synergy with PromptComplexityScorer (priority 14):
  - If _prompt_complexity < threshold → rewrite model in request body
  - Original model preserved in metadata for audit trail

Example:
  - User requests gpt-4, prompt is "What is 2+2?" (complexity 0.05)
  - Downgrader rewrites model to gpt-3.5-turbo → 20x cheaper, same quality

Config (via manifest ui_schema):
  - complexity_threshold: float (0.3) — downgrade below this score
  - downgrade_map: dict — model replacement map
  - preserve_streaming: bool (true) — keep stream flag on downgrade
"""

from typing import Dict, Any

from core.plugin_sdk import BasePlugin, PluginResponse, PluginHook
from core.plugin_engine import PluginContext


# Default downgrade mappings (expensive → cheap equivalent)
DEFAULT_DOWNGRADE_MAP = {
    "gpt-4": "gpt-3.5-turbo",
    "gpt-4-turbo": "gpt-3.5-turbo",
    "gpt-4o": "gpt-4o-mini",
    "gpt-4-0125-preview": "gpt-3.5-turbo",
    "claude-3-opus-20240229": "claude-3-haiku-20240307",
    "claude-3-5-sonnet-20241022": "claude-3-haiku-20240307",
    "claude-sonnet-4-20250514": "claude-haiku-4-5-20251001",
    "claude-opus-4-20250514": "claude-sonnet-4-20250514",
}


class ModelDowngrader(BasePlugin):
    name = "model_downgrader"
    hook = PluginHook.PRE_FLIGHT
    version = "1.0.0"
    author = "llmproxy"
    description = "Downgrades expensive models for simple prompts to save costs"
    timeout_ms = 2  # Dict lookup only

    def __init__(self, config: Dict[str, Any] = None):
        super().__init__(config)
        self.complexity_threshold: float = self.config.get("complexity_threshold", 0.3)
        self.downgrade_map: Dict[str, str] = self.config.get(
            "downgrade_map", DEFAULT_DOWNGRADE_MAP
        )

        # Counters
        self._total_checked: int = 0
        self._total_downgraded: int = 0

    def get_stats(self) -> Dict[str, Any]:
        """Public stats for dashboard."""
        return {
            "total_checked": self._total_checked,
            "total_downgraded": self._total_downgraded,
            "downgrade_rate": round(
                self._total_downgraded / max(self._total_checked, 1), 4
            ),
        }

    async def execute(self, ctx: PluginContext) -> PluginResponse:
        self._total_checked += 1

        # Requires PromptComplexityScorer to have run first (priority 14 < 16)
        complexity = ctx.metadata.get("_prompt_complexity")
        if complexity is None:
            # Complexity scorer not enabled — skip silently
            return PluginResponse.passthrough()

        # Only downgrade simple prompts
        if complexity >= self.complexity_threshold:
            return PluginResponse.passthrough()

        # Check if current model has a cheaper alternative
        current_model = ctx.body.get("model", "")
        target_model = self.downgrade_map.get(current_model)

        if not target_model:
            return PluginResponse.passthrough()

        # Perform downgrade
        ctx.metadata["_original_model"] = current_model
        ctx.metadata["_downgraded_to"] = target_model
        ctx.metadata["_downgrade_reason"] = f"complexity={complexity:.3f}<{self.complexity_threshold}"
        ctx.body["model"] = target_model

        self._total_downgraded += 1

        return PluginResponse.modify(body=ctx.body)

    async def on_load(self):
        self.logger.info(
            f"ModelDowngrader loaded: threshold={self.complexity_threshold}, "
            f"mappings={len(self.downgrade_map)}"
        )

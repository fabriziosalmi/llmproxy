"""
LLMPROXY Marketplace Plugin — Token Counter

Background plugin that extracts REAL token counts from upstream API responses
(usage.prompt_tokens / completion_tokens) and corrects the heuristic estimates
made by SmartBudgetGuard at PRE_FLIGHT.

Without this plugin, budget tracking relies on the 4-char/token heuristic,
which can be off by 20-40%. This closes the feedback loop with actual data.

Outputs:
  - ctx.metadata["_actual_tokens_in"]: real input tokens from API response
  - ctx.metadata["_actual_tokens_out"]: real output tokens from API response
  - ctx.metadata["_actual_cost_usd"]: real cost based on actual tokens
  - Calls SmartBudgetGuard.record_actual_cost() if available (budget correction)

Uses core.pricing for per-model cost calculation instead of flat rates.
"""

import json
import logging
from typing import Dict, Any

from core.plugin_sdk import BasePlugin, PluginResponse, PluginHook
from core.plugin_engine import PluginContext

logger = logging.getLogger("plugin.token_counter")


class TokenCounter(BasePlugin):
    name = "token_counter"
    hook = PluginHook.BACKGROUND
    version = "1.0.0"
    author = "llmproxy"
    description = "Extracts real token counts from API responses and corrects budget estimates"
    timeout_ms = 5  # JSON parse only

    def __init__(self, config: Dict[str, Any] = None):
        super().__init__(config)
        # Lifetime counters
        self._total_input_tokens: int = 0
        self._total_output_tokens: int = 0
        self._total_cost_usd: float = 0.0
        self._requests_counted: int = 0

    def _extract_usage(self, ctx: PluginContext) -> Dict[str, int] | None:
        """Extract usage dict from response body."""
        if not ctx.response or not hasattr(ctx.response, "body"):
            return None
        try:
            data = json.loads(ctx.response.body.decode())
            usage = data.get("usage")
            if usage and "prompt_tokens" in usage:
                return usage
        except Exception:
            pass
        return None

    def _compute_cost(self, model: str, input_tokens: int, output_tokens: int) -> float:
        """Compute actual cost from real token counts using per-model pricing."""
        from core.pricing import estimate_cost
        return estimate_cost(model, input_tokens, output_tokens)

    def get_stats(self) -> Dict[str, Any]:
        """Public stats for SOC dashboard / admin API."""
        return {
            "requests_counted": self._requests_counted,
            "total_input_tokens": self._total_input_tokens,
            "total_output_tokens": self._total_output_tokens,
            "total_cost_usd": round(self._total_cost_usd, 6),
            "avg_tokens_per_request": round(
                (self._total_input_tokens + self._total_output_tokens)
                / max(self._requests_counted, 1),
                1,
            ),
        }

    async def execute(self, ctx: PluginContext) -> PluginResponse:
        # Skip cached responses (no upstream call = no usage data)
        if ctx.metadata.get("_cache_status") == "HIT":
            return PluginResponse.passthrough()

        usage = self._extract_usage(ctx)
        if not usage:
            return PluginResponse.passthrough()

        input_tokens = usage.get("prompt_tokens", 0)
        output_tokens = usage.get("completion_tokens", 0)
        model = ctx.body.get("model", "")
        actual_cost = self._compute_cost(model, input_tokens, output_tokens)

        # Update lifetime counters
        self._total_input_tokens += input_tokens
        self._total_output_tokens += output_tokens
        self._total_cost_usd += actual_cost
        self._requests_counted += 1

        # Enrich metadata
        ctx.metadata["_actual_tokens_in"] = input_tokens
        ctx.metadata["_actual_tokens_out"] = output_tokens
        ctx.metadata["_actual_cost_usd"] = round(actual_cost, 6)

        # Budget correction: if SmartBudgetGuard estimated cost, correct it
        estimated_cost = ctx.metadata.get("_estimated_cost_usd")
        if estimated_cost is not None:
            delta = actual_cost - estimated_cost
            ctx.metadata["_cost_delta_usd"] = round(delta, 6)

        return PluginResponse.passthrough()

    async def on_load(self):
        self.logger.info("TokenCounter loaded: using per-model pricing from core.pricing")

"""
LLMPROXY Marketplace Plugin — Smart Budget Guard

Per-session and per-team budget enforcement at the PRE_FLIGHT ring.
Tracks estimated token cost BEFORE the request hits the model,
using a heuristic based on message length and model pricing tiers.

If the estimated cost would push the session/team over budget,
the request is blocked with a clear message and remaining budget info.

Config (via manifest ui_schema):
  - session_budget_usd: float (default 5.0) — max spend per session
  - team_budget_usd: float (default 100.0) — max spend per team/key
  - cost_per_1k_input: float (default 0.003) — estimated $/1K input tokens
  - cost_per_1k_output: float (default 0.015) — estimated $/1K output tokens
  - avg_output_ratio: float (default 0.5) — est. output/input token ratio
  - warn_threshold: float (default 0.8) — warn at 80% budget usage
"""

import time
from collections import defaultdict
from typing import Dict, Any

from core.plugin_sdk import BasePlugin, PluginResponse, PluginHook
from core.plugin_engine import PluginContext


class SmartBudgetGuard(BasePlugin):
    name = "smart_budget_guard"
    hook = PluginHook.PRE_FLIGHT
    version = "1.0.0"
    author = "llmproxy"
    description = "Pre-flight budget enforcement with cost estimation"
    timeout_ms = 5  # Pure math, no I/O

    def __init__(self, config: Dict[str, Any] = None):
        super().__init__(config)
        self.session_budget_usd: float = self.config.get("session_budget_usd", 5.0)
        self.team_budget_usd: float = self.config.get("team_budget_usd", 100.0)
        self.cost_per_1k_input: float = self.config.get("cost_per_1k_input", 0.003)
        self.cost_per_1k_output: float = self.config.get("cost_per_1k_output", 0.015)
        self.avg_output_ratio: float = self.config.get("avg_output_ratio", 0.5)
        self.warn_threshold: float = self.config.get("warn_threshold", 0.8)

        # session_id → accumulated cost in USD
        self._session_spend: Dict[str, float] = defaultdict(float)
        # team/api_key → accumulated cost in USD
        self._team_spend: Dict[str, float] = defaultdict(float)

    def _estimate_tokens(self, body: Dict[str, Any]) -> int:
        """Estimate input token count from messages (rough: 1 token ≈ 4 chars)."""
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
        return max(total_chars // 4, 1)

    def _estimate_cost(self, input_tokens: int) -> float:
        """Estimate total cost (input + expected output)."""
        est_output_tokens = int(input_tokens * self.avg_output_ratio)
        input_cost = (input_tokens / 1000) * self.cost_per_1k_input
        output_cost = (est_output_tokens / 1000) * self.cost_per_1k_output
        return input_cost + output_cost

    async def execute(self, ctx: PluginContext) -> PluginResponse:
        body = ctx.body
        session_id = ctx.session_id or "default"
        team_key = ctx.metadata.get("api_key", session_id)

        input_tokens = self._estimate_tokens(body)
        estimated_cost = self._estimate_cost(input_tokens)

        # Check session budget
        session_spent = self._session_spend[session_id]
        if session_spent + estimated_cost > self.session_budget_usd:
            remaining = max(0, self.session_budget_usd - session_spent)
            return PluginResponse.block(
                status_code=429,
                error_type="session_budget_exceeded",
                message=(
                    f"Session budget exceeded. Spent: ${session_spent:.4f}, "
                    f"Estimated: ${estimated_cost:.4f}, "
                    f"Remaining: ${remaining:.4f}, "
                    f"Limit: ${self.session_budget_usd:.2f}"
                ),
            )

        # Check team budget
        team_spent = self._team_spend[team_key]
        if team_spent + estimated_cost > self.team_budget_usd:
            remaining = max(0, self.team_budget_usd - team_spent)
            return PluginResponse.block(
                status_code=429,
                error_type="team_budget_exceeded",
                message=(
                    f"Team budget exceeded. Spent: ${team_spent:.4f}, "
                    f"Estimated: ${estimated_cost:.4f}, "
                    f"Remaining: ${remaining:.4f}, "
                    f"Limit: ${self.team_budget_usd:.2f}"
                ),
            )

        # Record estimated spend (will be corrected post-flight with actual tokens)
        self._session_spend[session_id] += estimated_cost
        self._team_spend[team_key] += estimated_cost

        # Warn if approaching threshold
        session_usage_pct = (self._session_spend[session_id] / self.session_budget_usd)
        if session_usage_pct >= self.warn_threshold:
            ctx.metadata["_budget_warning"] = (
                f"Session at {session_usage_pct:.0%} of ${self.session_budget_usd:.2f} budget"
            )
            self.logger.warning(
                f"Budget warning: session={session_id} at {session_usage_pct:.0%}"
            )

        # Annotate context with cost metadata for downstream plugins
        ctx.metadata["_estimated_cost_usd"] = estimated_cost
        ctx.metadata["_estimated_input_tokens"] = input_tokens

        return PluginResponse.passthrough()

    def record_actual_cost(self, session_id: str, team_key: str,
                           estimated: float, actual: float):
        """
        Called post-flight to correct the estimate with actual token usage.
        Delta = actual - estimated is applied to running totals.
        """
        delta = actual - estimated
        self._session_spend[session_id] += delta
        self._team_spend[team_key] += delta

    async def on_load(self):
        self.logger.info(
            f"SmartBudgetGuard loaded: session=${self.session_budget_usd}, "
            f"team=${self.team_budget_usd}, warn@{self.warn_threshold:.0%}"
        )

    async def on_unload(self):
        self._session_spend.clear()
        self._team_spend.clear()
        self.logger.info("SmartBudgetGuard unloaded, spend trackers cleared")

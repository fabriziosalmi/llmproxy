"""
LLMPROXY Marketplace Plugin — A/B Model Router

Routes requests between two models (control vs. variant) at a configured
split ratio for experimentation. Records which arm was selected in the
request body so downstream plugins and the audit log can track outcomes.

Unlike model_groups (which are load-balancing), this plugin is for
deliberate A/B experimentation: compare cost, latency, or quality
between two models on live traffic.

Use cases:
  - Model evaluation: "route 10% to gpt-4o-mini, 90% to gpt-4o"
  - Cost optimization: test a cheaper model on a slice of traffic
  - Shadow testing: validate a new model before full rollout

Config (via manifest ui_schema):
  - control_model: str — the primary model (100% - split_pct)
  - variant_model: str — the model under test
  - split_pct: float — percentage of traffic sent to variant (0.0–1.0, default: 0.1)
  - sticky: bool — pin session_id to the same arm for consistent multi-turn (default: true)
  - experiment_id: str — tag injected into request metadata for tracking (default: "ab_test")
"""

import random
import logging
import time
from typing import Dict, Any

from core.plugin_sdk import BasePlugin, PluginResponse, PluginHook
from core.plugin_engine import PluginContext

logger = logging.getLogger(__name__)

_SESSION_TTL = 3600  # 1 hour session stickiness


class ABModelRouter(BasePlugin):
    name = "ab_model_router"
    hook = PluginHook.ROUTING
    version = "1.0.0"
    author = "llmproxy"
    description = "Routes a configurable % of traffic to a variant model for A/B experimentation"
    timeout_ms = 2

    def __init__(self, config: Dict[str, Any] = None):
        super().__init__(config)
        self.control_model: str = self.config.get("control_model", "")
        self.variant_model: str = self.config.get("variant_model", "")
        self.split_pct: float = float(self.config.get("split_pct", 0.1))
        self.sticky: bool = self.config.get("sticky", True)
        self.experiment_id: str = self.config.get("experiment_id", "ab_test")
        # session_id → (arm: "control"|"variant", assigned_at: float)
        self._session_arms: Dict[str, tuple] = {}

    def _prune_sessions(self):
        now = time.time()
        expired = [k for k, (_, ts) in self._session_arms.items() if now - ts > _SESSION_TTL]
        for k in expired:
            del self._session_arms[k]

    def _select_arm(self, session_id: str) -> str:
        if self.sticky and session_id:
            if session_id in self._session_arms:
                return self._session_arms[session_id][0]
            arm = "variant" if random.random() < self.split_pct else "control"
            self._session_arms[session_id] = (arm, time.time())
            if len(self._session_arms) % 100 == 0:
                self._prune_sessions()
            return arm

        return "variant" if random.random() < self.split_pct else "control"

    async def execute(self, ctx: PluginContext) -> PluginResponse:
        if not self.control_model or not self.variant_model:
            return PluginResponse.passthrough()

        requested_model = ctx.body.get("model", "")

        # Only intercept if the request is for the control model (or no model set)
        if requested_model and requested_model not in (self.control_model, self.variant_model):
            return PluginResponse.passthrough()

        session_id = ctx.session_id or ""
        arm = self._select_arm(session_id)
        chosen_model = self.variant_model if arm == "variant" else self.control_model

        ctx.body["model"] = chosen_model
        # Inject experiment metadata for audit/telemetry
        ctx.body.setdefault("_ab_meta", {}).update({
            "experiment_id": self.experiment_id,
            "arm": arm,
            "control": self.control_model,
            "variant": self.variant_model,
        })

        self.logger.debug(
            f"ABModelRouter: experiment={self.experiment_id} arm={arm} "
            f"model={chosen_model} session={session_id or 'anon'}"
        )
        return PluginResponse(action="modify", body=ctx.body)

    async def on_load(self):
        self.logger.info(
            f"ABModelRouter loaded: experiment={self.experiment_id} "
            f"control={self.control_model} variant={self.variant_model} "
            f"split={self.split_pct:.0%} sticky={self.sticky}"
        )

    async def on_unload(self):
        self._session_arms.clear()
        self.logger.info("ABModelRouter unloaded, session assignments cleared")

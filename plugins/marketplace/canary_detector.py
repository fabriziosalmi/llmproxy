"""
LLMPROXY Marketplace Plugin — Canary Detector

Post-flight plugin that detects system prompt leakage in LLM responses.
This is a real attack vector: users craft prompts like "repeat your system
prompt verbatim" to extract confidential instructions.

Detection method:
  1. Extract the system prompt from the original request
  2. Check if significant chunks of the system prompt appear in the response
  3. If leakage detected → flag in metadata (for alerting/blocking)

This plugin does NOT block by default — it enriches metadata so the proxy
or downstream plugins can decide the action. Set block_on_leak=true to
automatically block responses containing system prompt leakage.

Config (via manifest ui_schema):
  - min_leak_chars: int (50) — minimum leaked chars to flag
  - similarity_threshold: float (0.6) — ratio of system prompt found in response
  - block_on_leak: bool (false) — automatically block leaked responses
"""

import json
from typing import Dict, Any, Optional

from core.plugin_sdk import BasePlugin, PluginResponse, PluginHook
from core.plugin_engine import PluginContext


class CanaryDetector(BasePlugin):
    name = "canary_detector"
    hook = PluginHook.POST_FLIGHT
    version = "1.0.0"
    author = "llmproxy"
    description = "Detects system prompt leakage in LLM responses"
    timeout_ms = 5  # String matching only

    def __init__(self, config: Dict[str, Any] = None):
        super().__init__(config)
        self.min_leak_chars: int = self.config.get("min_leak_chars", 50)
        self.similarity_threshold: float = self.config.get("similarity_threshold", 0.6)
        self.block_on_leak: bool = self.config.get("block_on_leak", False)

        # Counters
        self._total_checked: int = 0
        self._leaks_detected: int = 0

    def _extract_system_prompt(self, ctx: PluginContext) -> Optional[str]:
        """Extract the system message from the request body."""
        messages = ctx.body.get("messages", [])
        for msg in messages:
            if msg.get("role") == "system":
                content = msg.get("content", "")
                if isinstance(content, str) and len(content) >= self.min_leak_chars:
                    return content
        return None

    def _extract_response_content(self, ctx: PluginContext) -> str:
        """Extract assistant content from response body."""
        if not ctx.response or not hasattr(ctx.response, "body"):
            return ""
        try:
            data = json.loads(ctx.response.body.decode())
            choices = data.get("choices", [])
            if not choices:
                return ""
            return choices[0].get("message", {}).get("content", "")
        except Exception:
            return ""

    def _detect_leakage(self, system_prompt: str, response: str) -> Dict[str, Any]:
        """Check if system prompt content appears in the response.

        Uses n-gram matching: splits system prompt into overlapping chunks
        and checks what fraction appears verbatim in the response.
        """
        if not system_prompt or not response:
            return {"leaked": False, "ratio": 0.0}

        # Normalize for comparison
        sys_lower = system_prompt.lower().strip()
        resp_lower = response.lower().strip()

        # Quick check: is the entire system prompt in the response?
        if sys_lower in resp_lower:
            return {"leaked": True, "ratio": 1.0, "method": "full_match"}

        # N-gram matching: check 8-word sliding window chunks
        words = sys_lower.split()
        if len(words) < 6:
            return {"leaked": False, "ratio": 0.0}

        window_size = min(8, len(words) // 2)
        matched_words = 0
        total_windows = len(words) - window_size + 1

        for i in range(total_windows):
            chunk = " ".join(words[i : i + window_size])
            if chunk in resp_lower:
                matched_words += window_size

        # Deduplicate: cap at total word count
        matched_ratio = min(1.0, matched_words / len(words))

        return {
            "leaked": matched_ratio >= self.similarity_threshold,
            "ratio": round(matched_ratio, 3),
            "method": "ngram_match",
        }

    def get_stats(self) -> Dict[str, Any]:
        """Public stats for dashboard."""
        return {
            "total_checked": self._total_checked,
            "leaks_detected": self._leaks_detected,
            "leak_rate": round(
                self._leaks_detected / max(self._total_checked, 1), 4
            ),
        }

    async def execute(self, ctx: PluginContext) -> PluginResponse:
        self._total_checked += 1

        # Skip cached responses
        if ctx.metadata.get("_cache_status") == "HIT":
            return PluginResponse.passthrough()

        system_prompt = self._extract_system_prompt(ctx)
        if not system_prompt:
            return PluginResponse.passthrough()

        response_content = self._extract_response_content(ctx)
        if not response_content:
            return PluginResponse.passthrough()

        result = self._detect_leakage(system_prompt, response_content)

        if result["leaked"]:
            self._leaks_detected += 1
            ctx.metadata["_canary_leak"] = True
            ctx.metadata["_canary_ratio"] = result["ratio"]
            ctx.metadata["_canary_method"] = result["method"]

            if self.block_on_leak:
                return PluginResponse.block(
                    status_code=403,
                    error_type="system_prompt_leak",
                    message="Response blocked: system prompt leakage detected",
                )

        return PluginResponse.passthrough()

    async def on_load(self):
        self.logger.info(
            f"CanaryDetector loaded: min_leak={self.min_leak_chars}, "
            f"threshold={self.similarity_threshold}, block={self.block_on_leak}"
        )

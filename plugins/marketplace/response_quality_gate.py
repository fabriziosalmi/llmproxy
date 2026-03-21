"""
LLMPROXY Marketplace Plugin — Response Quality Gate

Post-flight quality analysis of LLM responses. Detects common failure modes
that waste tokens and frustrate users:

  1. Empty/blank responses (model returned nothing)
  2. Ultra-short responses (< min_length chars for non-trivial prompts)
  3. Safety refusals ("I cannot assist with that", "As an AI...")
  4. Hallucinated apologies (model apologizes but doesn't answer)
  5. Truncated responses (response cut off mid-sentence)

On detection, sets metadata flags that the proxy can use for:
  - Automatic retry on a different endpoint
  - Client-facing quality headers (X-LLMProxy-Quality)
  - Dashboard alerting (quality degradation trends)

This plugin does NOT block — it enriches metadata. The proxy decides the action.

Config (via manifest ui_schema):
  - min_length: int (20) — minimum response length for non-trivial prompts
  - refusal_threshold: int (2) — number of refusal patterns to flag
  - check_truncation: bool (true) — detect mid-sentence truncation
"""

import re
from typing import Dict, Any

from core.plugin_sdk import BasePlugin, PluginResponse, PluginHook
from core.plugin_engine import PluginContext


class ResponseQualityGate(BasePlugin):
    name = "response_quality_gate"
    hook = PluginHook.POST_FLIGHT
    version = "1.0.0"
    author = "llmproxy"
    description = "Detects empty, refused, or truncated LLM responses"
    timeout_ms = 5  # String matching only, no I/O

    # Refusal patterns (compiled once at class level)
    _REFUSAL_PATTERNS = [
        re.compile(r"\bI (?:cannot|can't|am unable to|won't|will not)\b", re.IGNORECASE),
        re.compile(r"\bAs an AI\b", re.IGNORECASE),
        re.compile(r"\bI'?m (?:just |only )?(?:a |an )?(?:language model|AI|assistant)\b", re.IGNORECASE),
        re.compile(r"\bI (?:don't|do not) (?:have )?(?:the ability|access|capability)\b", re.IGNORECASE),
        re.compile(r"\bsorry,? (?:but )?I (?:cannot|can't)\b", re.IGNORECASE),
        re.compile(r"\bnot (?:able|allowed|permitted) to\b", re.IGNORECASE),
        re.compile(r"\boutside (?:of )?my (?:capabilities|scope)\b", re.IGNORECASE),
        re.compile(r"\bI (?:must |need to )?(?:decline|refuse)\b", re.IGNORECASE),
    ]

    # Apology-without-substance patterns
    _APOLOGY_PATTERNS = [
        re.compile(r"\bI apologize\b", re.IGNORECASE),
        re.compile(r"\bI'?m sorry\b", re.IGNORECASE),
        re.compile(r"\bmy apologies\b", re.IGNORECASE),
    ]

    def __init__(self, config: Dict[str, Any] = None):
        super().__init__(config)
        self.min_length: int = self.config.get("min_length", 20)
        self.refusal_threshold: int = self.config.get("refusal_threshold", 2)
        self.check_truncation: bool = self.config.get("check_truncation", True)

    def _extract_response_content(self, ctx: PluginContext) -> str:
        """Extract assistant message content from response body."""
        if not ctx.response or not hasattr(ctx.response, "body"):
            return ""
        try:
            import json
            data = json.loads(ctx.response.body.decode())
            choices = data.get("choices", [])
            if not choices:
                return ""
            return choices[0].get("message", {}).get("content", "")
        except Exception:
            return ""

    def _is_trivial_prompt(self, ctx: PluginContext) -> bool:
        """Check if the prompt is trivially short (e.g., 'hi', 'hello')."""
        messages = ctx.body.get("messages", [])
        if not messages:
            return True
        last_content = messages[-1].get("content", "")
        if isinstance(last_content, str):
            return len(last_content.strip()) < 10
        return False

    def _check_refusals(self, content: str) -> int:
        """Count how many refusal patterns match."""
        count = 0
        for pattern in self._REFUSAL_PATTERNS:
            if pattern.search(content):
                count += 1
        return count

    def _check_apology_only(self, content: str) -> bool:
        """Detect responses that are mostly apologies with no substance."""
        has_apology = any(p.search(content) for p in self._APOLOGY_PATTERNS)
        if not has_apology:
            return False
        # If response is short and mostly apology, flag it
        sentences = [s.strip() for s in re.split(r"[.!?]+", content) if s.strip()]
        if len(sentences) <= 2:
            return True
        return False

    def _check_truncation(self, content: str) -> bool:
        """Detect mid-sentence truncation (no terminal punctuation)."""
        if not content or len(content) < 50:
            return False
        stripped = content.rstrip()
        if not stripped:
            return False
        # Ends with code block or list → not truncated
        if stripped.endswith("```") or stripped.endswith(")"):
            return False
        # Normal text should end with punctuation
        last_char = stripped[-1]
        return last_char not in ".!?:;\"')]}`"

    async def execute(self, ctx: PluginContext) -> PluginResponse:
        content = self._extract_response_content(ctx)
        issues = []

        # Skip quality check if response is from cache
        if ctx.metadata.get("_cache_status") == "HIT":
            ctx.metadata["_quality_score"] = 1.0
            ctx.metadata["_quality_status"] = "cached"
            return PluginResponse.passthrough()

        # 1. Empty response
        if not content or not content.strip():
            issues.append("empty_response")

        # 2. Ultra-short response (only for non-trivial prompts)
        elif not self._is_trivial_prompt(ctx) and len(content.strip()) < self.min_length:
            issues.append("too_short")

        # 3. Safety refusals
        if content:
            refusal_count = self._check_refusals(content)
            if refusal_count >= self.refusal_threshold:
                issues.append("safety_refusal")

            # 4. Apology-only response
            if self._check_apology_only(content):
                issues.append("apology_only")

            # 5. Truncation
            if self.check_truncation and self._check_truncation(content):
                issues.append("truncated")

        # Compute quality score
        if not issues:
            quality_score = 1.0
            quality_status = "ok"
        elif "empty_response" in issues:
            quality_score = 0.0
            quality_status = "failed"
        elif "safety_refusal" in issues:
            quality_score = 0.2
            quality_status = "refused"
        else:
            quality_score = 0.5
            quality_status = "degraded"

        # Enrich metadata
        ctx.metadata["_quality_score"] = quality_score
        ctx.metadata["_quality_status"] = quality_status
        if issues:
            ctx.metadata["_quality_issues"] = issues

        return PluginResponse.passthrough()

    async def on_load(self):
        self.logger.info(
            f"ResponseQualityGate loaded: min_length={self.min_length}, "
            f"refusal_threshold={self.refusal_threshold}, "
            f"check_truncation={self.check_truncation}"
        )

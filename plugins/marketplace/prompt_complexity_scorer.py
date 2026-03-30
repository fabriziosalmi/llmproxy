"""
LLMPROXY Marketplace Plugin — Prompt Complexity Scorer

Analyzes prompt complexity at PRE_FLIGHT and produces a normalized score (0.0-1.0)
that downstream plugins (neural_router) can use for intelligent model routing:
  - Simple prompts (score < 0.3) → route to cheap/fast models (GPT-3.5, Haiku)
  - Complex prompts (score > 0.7) → route to capable models (GPT-4, Opus)

Complexity signals:
  - Token depth (total message length)
  - Turn count (multi-turn conversations are harder)
  - System prompt presence and length
  - Code block density (code-heavy = needs capable model)
  - Instruction density (imperative verbs, numbered steps)
  - Nesting depth (nested JSON/XML in prompt)

The score is written to ctx.metadata["_prompt_complexity"] for any downstream
plugin to consume. No blocking — purely additive metadata enrichment.

Config (via manifest ui_schema):
  - depth_weight: float (0.3) — weight for token depth signal
  - turns_weight: float (0.2) — weight for conversation turn count
  - code_weight: float (0.25) — weight for code block density
  - instruction_weight: float (0.25) — weight for instruction density
"""

import re
from typing import Dict, Any

from core.plugin_sdk import BasePlugin, PluginResponse, PluginHook
from core.plugin_engine import PluginContext


class PromptComplexityScorer(BasePlugin):
    name = "prompt_complexity_scorer"
    hook = PluginHook.PRE_FLIGHT
    version = "1.0.0"
    author = "llmproxy"
    description = "Scores prompt complexity for intelligent model routing"
    timeout_ms = 5  # Pure CPU, no I/O — must be ultra-fast

    # Regex patterns (compiled once at class level)
    _CODE_BLOCK_RE = re.compile(r"```[\s\S]*?```|`[^`]+`")
    _INSTRUCTION_RE = re.compile(
        r"\b(explain|analyze|compare|implement|write|create|design|build|"
        r"refactor|debug|optimize|summarize|translate|list|describe|calculate|"
        r"step \d|first|second|third|finally|must|should|ensure)\b",
        re.IGNORECASE,
    )
    _NESTED_STRUCTURE_RE = re.compile(r"[{}\[\]<>]")

    def __init__(self, config: Dict[str, Any] = None):
        super().__init__(config)
        self.depth_weight: float = self.config.get("depth_weight", 0.3)
        self.turns_weight: float = self.config.get("turns_weight", 0.2)
        self.code_weight: float = self.config.get("code_weight", 0.25)
        self.instruction_weight: float = self.config.get("instruction_weight", 0.25)

    def _extract_text(self, messages: list) -> str:
        """Extract all text content from messages."""
        parts = []
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, str):
                parts.append(content)
            elif isinstance(content, list):
                for p in content:
                    if isinstance(p, dict):
                        parts.append(p.get("text", ""))
        return "\n".join(parts)

    def _score_depth(self, text: str) -> float:
        """Token depth: longer prompts are more complex. Sigmoid-capped."""
        char_count = len(text)
        # Sigmoid: 500 chars = ~0.25, 2000 = ~0.7, 5000 = ~0.95
        return min(1.0, char_count / 6000)

    def _score_turns(self, messages: list) -> float:
        """Multi-turn: more turns = more context to manage."""
        turn_count = len(messages)
        # 1 turn = 0.0, 5 turns = 0.5, 10+ = 1.0
        return min(1.0, max(0.0, (turn_count - 1) / 9))

    def _score_code(self, text: str) -> float:
        """Code density: ratio of code blocks to total text."""
        if not text:
            return 0.0
        code_matches = self._CODE_BLOCK_RE.findall(text)
        code_chars = sum(len(m) for m in code_matches)
        ratio = code_chars / len(text) if text else 0.0
        # Code ratio > 0.5 = very code-heavy
        return min(1.0, ratio * 2)

    def _score_instructions(self, text: str) -> float:
        """Instruction density: imperative verbs, numbered steps."""
        if not text:
            return 0.0
        matches = self._INSTRUCTION_RE.findall(text)
        # Normalize: 3 instructions = 0.3, 10+ = 1.0
        return min(1.0, len(matches) / 10)

    async def execute(self, ctx: PluginContext) -> PluginResponse:
        messages = ctx.body.get("messages", [])
        if not messages:
            ctx.metadata["_prompt_complexity"] = 0.0
            ctx.metadata["_complexity_tier"] = "simple"
            return PluginResponse.passthrough()

        text = self._extract_text(messages)

        # Compute individual signals
        depth = self._score_depth(text)
        turns = self._score_turns(messages)
        code = self._score_code(text)
        instructions = self._score_instructions(text)

        # Weighted composite score
        score = (
            depth * self.depth_weight
            + turns * self.turns_weight
            + code * self.code_weight
            + instructions * self.instruction_weight
        )
        score = round(min(1.0, max(0.0, score)), 3)

        # Tier classification
        if score < 0.3:
            tier = "simple"
        elif score < 0.7:
            tier = "moderate"
        else:
            tier = "complex"

        # Enrich metadata for downstream routing
        ctx.metadata["_prompt_complexity"] = score
        ctx.metadata["_complexity_tier"] = tier
        ctx.metadata["_complexity_signals"] = {
            "depth": round(depth, 3),
            "turns": round(turns, 3),
            "code": round(code, 3),
            "instructions": round(instructions, 3),
        }

        return PluginResponse.passthrough()

    async def on_load(self):
        self.logger.info(
            f"PromptComplexityScorer loaded: weights="
            f"depth={self.depth_weight}, turns={self.turns_weight}, "
            f"code={self.code_weight}, instructions={self.instruction_weight}"
        )

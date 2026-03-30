"""
LLMPROXY Marketplace Plugin — Agentic Loop Breaker

Detects AI agents stuck in retry loops by tracking prompt hashes per session.
When the same (or near-identical) prompt appears N times in a sliding window,
the plugin blocks the request and returns a diagnostic message.

Use case: Agentic frameworks (AutoGPT, CrewAI, LangGraph) sometimes enter
infinite loops where the agent repeatedly sends the same prompt. This plugin
catches the loop early, saving tokens and cost.

Config (via manifest ui_schema):
  - max_repeats: int (default 3) — how many identical prompts trigger block
  - window_seconds: int (default 120) — sliding window for repeat detection
  - hash_messages: int (default 3) — how many trailing messages to hash
"""

import time
import hashlib
from collections import defaultdict
from typing import Dict, Any, List, Tuple

from core.plugin_sdk import BasePlugin, PluginResponse, PluginHook
from core.plugin_engine import PluginContext


class AgenticLoopBreaker(BasePlugin):
    name = "agentic_loop_breaker"
    hook = PluginHook.PRE_FLIGHT
    version = "1.0.0"
    author = "llmproxy"
    description = "Detects and breaks agentic retry loops via prompt hashing"
    timeout_ms = 10  # Must be fast — hash-only logic

    def __init__(self, config: Dict[str, Any] = None):
        super().__init__(config)
        self.max_repeats: int = self.config.get("max_repeats", 3)
        self.window_seconds: int = self.config.get("window_seconds", 120)
        self.hash_messages: int = self.config.get("hash_messages", 3)
        # session_id → list of (timestamp, hash)
        self._session_hashes: Dict[str, List[Tuple[float, str]]] = defaultdict(list)

    def _compute_prompt_hash(self, body: Dict[str, Any]) -> str:
        """Hash the trailing N messages to detect repeated prompts."""
        messages = body.get("messages", [])
        if not messages:
            return ""
        # Take the last N messages for fingerprinting
        tail = messages[-self.hash_messages:]
        # Normalize: role + content only, strip whitespace
        normalized = ""
        for msg in tail:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if isinstance(content, list):
                # Multi-modal: extract text parts
                content = " ".join(
                    p.get("text", "") for p in content if isinstance(p, dict)
                )
            normalized += f"{role}:{content.strip()}|"
        return hashlib.sha256(normalized.encode()).hexdigest()[:16]

    def _prune_window(self, session_id: str, now: float):
        """Remove entries outside the sliding window."""
        cutoff = now - self.window_seconds
        entries = self._session_hashes[session_id]
        self._session_hashes[session_id] = [
            (ts, h) for ts, h in entries if ts >= cutoff
        ]

    async def execute(self, ctx: PluginContext) -> PluginResponse:
        body = ctx.body
        if not body.get("messages"):
            return PluginResponse.passthrough()

        prompt_hash = self._compute_prompt_hash(body)
        if not prompt_hash:
            return PluginResponse.passthrough()

        session_id = ctx.session_id or "default"
        now = time.time()

        # Prune old entries
        self._prune_window(session_id, now)

        # Count occurrences of this hash in the window
        entries = self._session_hashes[session_id]
        repeat_count = sum(1 for _, h in entries if h == prompt_hash)

        # Record this occurrence
        entries.append((now, prompt_hash))

        if repeat_count >= self.max_repeats:
            self.logger.warning(
                f"Loop detected: session={session_id} hash={prompt_hash} "
                f"repeats={repeat_count + 1}/{self.max_repeats}"
            )
            # Clear session hashes to allow retry after break
            self._session_hashes[session_id].clear()

            return PluginResponse.block(
                status_code=429,
                error_type="agentic_loop_detected",
                message=(
                    f"Agentic loop detected: the same prompt was repeated "
                    f"{repeat_count + 1} times in {self.window_seconds}s. "
                    f"Request blocked to prevent infinite loop. "
                    f"Please revise your agent's logic."
                ),
            )

        return PluginResponse.passthrough()

    async def on_load(self):
        self.logger.info(
            f"AgenticLoopBreaker loaded: max_repeats={self.max_repeats}, "
            f"window={self.window_seconds}s, hash_messages={self.hash_messages}"
        )

    async def on_unload(self):
        self._session_hashes.clear()
        self.logger.info("AgenticLoopBreaker unloaded, session hashes cleared")

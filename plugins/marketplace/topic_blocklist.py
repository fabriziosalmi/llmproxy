"""
LLMPROXY Marketplace Plugin — Topic Blocklist

Declarative keyword/regex blocklist for incoming LLM requests.
Scans all message content against a configurable list of blocked topics
and returns 400 with a clear error when a match is found.

Config (via manifest ui_schema):
  - topics: list[str] — keywords or regex patterns to block
  - action: "block" | "warn" | "log" — what to do on match (default: block)
  - match_mode: "keyword" | "whole_word" | "regex" — matching strategy (default: keyword)
  - case_sensitive: bool — case-sensitive matching (default: false)
  - scan_roles: list[str] — which roles to scan (default: ["user"])

Use case: Enterprise compliance ("no crypto trading advice"),
  child safety filters, internal policy enforcement (no competitor mentions),
  or GDPR topic restrictions.
"""

import re
import logging
from typing import Dict, Any, List, Optional

from core.plugin_sdk import BasePlugin, PluginResponse, PluginHook
from core.plugin_engine import PluginContext

logger = logging.getLogger(__name__)

_DEFAULT_TOPICS = [
    "how to make a bomb",
    "how to make explosives",
    "child sexual abuse",
    "csam",
]


def _extract_text(messages: List[Dict[str, Any]], scan_roles: List[str]) -> str:
    """Extract plain text from messages for the given roles."""
    parts = []
    for msg in messages:
        if msg.get("role") not in scan_roles:
            continue
        content = msg.get("content", "")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            # Multimodal: extract text parts only
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    parts.append(part.get("text", ""))
    return "\n".join(parts)


def _compile_patterns(
    topics: List[str], match_mode: str, case_sensitive: bool
) -> List[tuple]:
    """Pre-compile patterns for efficient matching. Returns list of (pattern, topic_str)."""
    flags = 0 if case_sensitive else re.IGNORECASE
    compiled = []
    for topic in topics:
        if not topic or not topic.strip():
            continue
        if match_mode == "regex":
            try:
                compiled.append((re.compile(topic, flags), topic))
            except re.error as e:
                logger.warning(f"TopicBlocklist: invalid regex '{topic}': {e}")
        elif match_mode == "whole_word":
            escaped = re.escape(topic.strip())
            compiled.append((re.compile(rf"\b{escaped}\b", flags), topic))
        else:  # keyword (default) — simple substring via re for case handling
            escaped = re.escape(topic.strip())
            compiled.append((re.compile(escaped, flags), topic))
    return compiled


class TopicBlocklist(BasePlugin):
    name = "topic_blocklist"
    hook = PluginHook.PRE_FLIGHT
    version = "1.0.0"
    author = "llmproxy"
    description = "Blocks requests containing forbidden topics via keyword/regex matching"
    timeout_ms = 5

    def __init__(self, config: Dict[str, Any] = None):
        super().__init__(config)
        self.topics: List[str] = self.config.get("topics", _DEFAULT_TOPICS)
        self.action: str = self.config.get("action", "block")
        self.match_mode: str = self.config.get("match_mode", "keyword")
        self.case_sensitive: bool = self.config.get("case_sensitive", False)
        self.scan_roles: List[str] = self.config.get("scan_roles", ["user"])
        self._patterns = _compile_patterns(self.topics, self.match_mode, self.case_sensitive)

    def _find_match(self, text: str) -> Optional[str]:
        """Return the first matched topic string, or None."""
        for pattern, topic in self._patterns:
            if pattern.search(text):
                return topic
        return None

    async def execute(self, ctx: PluginContext) -> PluginResponse:
        messages = ctx.body.get("messages", [])
        if not messages or not self._patterns:
            return PluginResponse.passthrough()

        text = _extract_text(messages, self.scan_roles)
        if not text:
            return PluginResponse.passthrough()

        matched = self._find_match(text)
        if matched is None:
            return PluginResponse.passthrough()

        log_msg = f"TopicBlocklist: matched topic='{matched}' session={ctx.session_id or 'anon'}"

        if self.action == "log":
            logger.info(log_msg)
            return PluginResponse.passthrough()

        if self.action == "warn":
            logger.warning(log_msg)
            return PluginResponse.passthrough()

        # action == "block" (default)
        logger.warning(log_msg)
        return PluginResponse.block(
            status_code=400,
            error_type="topic_blocked",
            message=(
                f"Request blocked: content matches a restricted topic. "
                f"Topic: '{matched}'. Please revise your request."
            ),
        )

    async def on_load(self):
        self.logger.info(
            f"TopicBlocklist loaded: {len(self._patterns)} topics, "
            f"mode={self.match_mode}, action={self.action}, "
            f"scan_roles={self.scan_roles}"
        )

    async def on_unload(self):
        self._patterns.clear()
        self.logger.info("TopicBlocklist unloaded")

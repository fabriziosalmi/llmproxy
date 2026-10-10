"""Which tools a model may call, and when.

Detection asks whether a text looks like an attack, and on text it was not
written against it is wrong most of the time (docs/security/benchmark.md). This
asks something that has an answer: is this tool call one the operator allows,
at this point of the conversation.

Two rules, both about the tool calls in the model's response:

* ``allow`` / ``deny``: the tools that may be called at all.
* ``after_tool_result``: the tools that may be called in a turn that follows a
  tool result. A tool result is data from somewhere else (a web page, an
  inbox, a file). When the model answers one by calling a tool, nobody asked
  for that call except, possibly, the data. With this list set, such a call is
  refused unless the tool is on it; the usual choice is the read-only tools.
  A call the user really wants goes through as soon as the user says so: the
  turn then follows a user message, not a tool result.

That second rule is what stops an indirect injection, and it does so without
reading the injected text: the instruction can say anything, the call it asks
for is not one that may follow data.

It does not help when the untrusted text arrives inside the user's own message
(a pasted page, retrieved context placed in the user turn): the proxy cannot
tell those words from the user's.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from fnmatch import fnmatchcase
from typing import Any

#: Roles whose content is a tool's output.
_TOOL_ROLES = frozenset({"tool", "function"})


def _patterns(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, Iterable):
        return ()
    return tuple(str(v).strip() for v in value if str(v).strip())


def _matches(name: str, patterns: tuple[str, ...]) -> bool:
    # Case matters, on purpose. A tool name is an identifier, and folding case
    # makes a loose pattern looser: ``*get*`` matches ``ManageTrafficLightState``
    # (mana-GE-T-raffic), which is how a state-changing tool would have ended up
    # on a read-only list. Exact names are safer still than patterns.
    return any(fnmatchcase(name, p) for p in patterns)


@dataclass(frozen=True)
class ToolPolicy:
    enabled: bool = False
    #: False: say what would be refused and let it through (``mode: log_only``).
    enforce: bool = True
    allow: tuple[str, ...] = ("*",)
    deny: tuple[str, ...] = ()
    #: None: no restriction after a tool result.
    after_tool_result: tuple[str, ...] | None = None

    @classmethod
    def from_config(cls, config: Mapping[str, Any] | None) -> ToolPolicy:
        """The policy in ``security.tool_policy`` (disabled when absent)."""
        section = ((config or {}).get("security") or {}).get("tool_policy") or {}
        if not isinstance(section, Mapping) or not section.get("enabled", False):
            return cls()
        after = section.get("after_tool_result")
        return cls(
            enabled=True,
            enforce=str(section.get("mode", "enforce")).lower() != "log_only",
            allow=_patterns(section.get("allow", ["*"])),
            deny=_patterns(section.get("deny", [])),
            after_tool_result=None if after is None else _patterns(after),
        )

    def refusal(self, name: str, after_tool_result: bool) -> str | None:
        """Why ``name`` may not be called now; None when it may."""
        if not self.enabled:
            return None
        if _matches(name, self.deny):
            return "the tool is denied"
        if not _matches(name, self.allow):
            return "the tool is not on the allow list"
        if (
            after_tool_result
            and self.after_tool_result is not None
            and not _matches(name, self.after_tool_result)
        ):
            return "it may not be called in a turn that follows a tool result"
        return None


def follows_tool_result(messages: Any) -> bool:
    """Whether the model's next turn answers a tool result rather than the user.

    The assistant's own messages in between are skipped: an agent loop is
    ``user, assistant(call), tool, assistant(call), tool, ...`` and every turn
    after the first answers data. A user message resets it.
    """
    if not isinstance(messages, list):
        return False
    for message in reversed(messages):
        role = message.get("role") if isinstance(message, Mapping) else None
        if role == "assistant":
            continue
        return role in _TOOL_ROLES
    return False


def called_tools(message: Any) -> list[str]:
    """The names of the tools an assistant message (or a stream delta) calls."""
    if not isinstance(message, Mapping):
        return []
    names: list[str] = []
    for call in message.get("tool_calls") or []:
        fn = call.get("function") if isinstance(call, Mapping) else None
        if isinstance(fn, Mapping) and fn.get("name"):
            names.append(str(fn["name"]))
    legacy = message.get("function_call")
    if isinstance(legacy, Mapping) and legacy.get("name"):
        names.append(str(legacy["name"]))
    return names

"""Where the question "is authentication on?" gets exactly one answer.

`server.auth.enabled` used to be read in thirteen places with two different
defaults. Twelve runtime readers treated an absent key as False; the startup
validator treated it as True. So a config that simply omitted the auth section
validated as authenticated — the check demanded LLM_PROXY_API_KEYS and refused
to boot without it — and then served every request unauthenticated, because at
request time the same missing key meant the opposite. The operator did exactly
what the startup check asked and got an open proxy, with no warning anywhere.

The default here is True. For a security gateway an omitted auth section must
mean "authenticate, and tell me if you cannot" rather than "let everything
through". This is not a behaviour change for any deployment that starts today:
a config without the section already had to satisfy the startup validator's
demand for keys, and one that cannot satisfy it already fails to boot. What
changes is only the case that used to boot open.
"""

from __future__ import annotations

from typing import Any, Mapping

#: An absent `server.auth.enabled` means authentication is ON.
DEFAULT_AUTH_ENABLED = True


def auth_enabled(config: Mapping[str, Any] | None) -> bool:
    """True when the proxy must authenticate callers.

    Accepts anything mapping-like so callers can pass a raw parsed config, a
    hot-reloaded dict, or the live agent config without converting first.
    """
    if not config:
        return DEFAULT_AUTH_ENABLED
    server = config.get("server") or {}
    auth = server.get("auth") or {}
    value = auth.get("enabled", DEFAULT_AUTH_ENABLED)
    return bool(value)

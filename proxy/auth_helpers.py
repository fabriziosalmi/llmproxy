"""LLMProxy — Authentication helpers.

Pure functions for API-key resolution and constant-time comparison.
Stateless — the orchestrator calls them with config + token; tests can
exercise them directly.

Extracted from proxy/rotator.py to make the timing-safe verifier
reusable and independently testable.
"""

from __future__ import annotations

import hmac
from typing import Any, Dict, List

from core.secrets import SecretManager


def resolve_api_keys(config: Dict[str, Any]) -> List[str]:
    """Read the configured API key bag from secrets.

    Looks up `server.auth.api_keys_env` (defaulting to
    `LLM_PROXY_API_KEYS`) via the secret manager and splits the comma-
    separated value. Empty entries are dropped.
    """
    env_var = (
        config.get("server", {})
        .get("auth", {})
        .get("api_keys_env", "LLM_PROXY_API_KEYS")
    )
    raw = SecretManager.get_secret(env_var, "") or ""
    return [k.strip() for k in raw.split(",") if k.strip()]


def parse_bearer(auth_header: str) -> str:
    """Strip a single leading ``Bearer `` scheme prefix (case-insensitive).

    Historically the routes did ``auth_header.replace("Bearer ", "")`` — a
    *global* replace that mangles any key containing the substring ``Bearer ``
    and silently accepts malformed schemes. This strips exactly one prefix and
    otherwise returns the header verbatim (tolerating a raw token).
    """
    h = auth_header.strip()
    if h[:7].lower() == "bearer ":
        return h[7:].strip()
    return h


def resolve_admin_keys(config: Dict[str, Any]) -> List[str]:
    """Read the dedicated control-plane admin key bag.

    Looks up ``server.auth.admin_keys_env`` (default ``LLM_PROXY_ADMIN_KEYS``).
    Kept separate from the inference key bag so that an ordinary inference key
    cannot drive the control plane (config apply, plugin install, GDPR purge).
    Returns an empty list when unset.
    """
    env_var = (
        config.get("server", {})
        .get("auth", {})
        .get("admin_keys_env", "LLM_PROXY_ADMIN_KEYS")
    )
    raw = SecretManager.get_secret(env_var, "") or ""
    return [k.strip() for k in raw.split(",") if k.strip()]


def verify_admin_key(token: str, config: Dict[str, Any]) -> bool:
    """Constant-time check that ``token`` is a valid ADMIN key.

    When dedicated admin keys are configured, ONLY those grant control-plane
    access — inference keys are rejected. When none are configured, this falls
    back to the inference key bag so existing single-tier deployments keep
    working; operators should set ``LLM_PROXY_ADMIN_KEYS`` to segregate the
    control plane (any inference key = full admin until they do).
    """
    admin_keys = resolve_admin_keys(config)
    if admin_keys:
        return verify_api_key(token, admin_keys)
    return verify_api_key(token, resolve_api_keys(config))


def verify_api_key(token: str, valid_keys: List[str]) -> bool:
    """Constant-time membership check.

    `token in valid_keys` short-circuits on the first byte mismatch and
    on the first match — both leak timing. This OR-aggregates
    `compare_digest` across every configured key and never breaks early,
    so total runtime depends only on |valid_keys|, not on which key (if
    any) matched.

    Returns False on empty token or empty key set.
    """
    if not token:
        return False
    token_b = token.encode("utf-8", errors="replace")
    matched = False
    for k in valid_keys:
        if hmac.compare_digest(token_b, k.encode("utf-8", errors="replace")):
            matched = True
    return matched

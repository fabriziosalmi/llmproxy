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

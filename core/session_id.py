"""Derive a session identifier from a credential, without leaking it.

Three route modules each carried their own copy of this: SHA-256 of the bearer
token, truncated to sixteen hex characters, falling back to a hash of
IP + User-Agent + Accept-Language when auth is off. Same logic, three
transcriptions, and CodeQL flagged all three as
`py/weak-sensitive-data-hashing` — a fast hash applied to a secret.

The finding is right in principle even though the practical risk was small. A
session_id is not private: it is written into every audit row and appears in
log lines. A bare hash of a credential means anyone who sees one can test
candidate tokens offline and confirm a match. With 128-bit random keys that is
infeasible, so nothing was exploitable — but the safety rested on an assumption
about key entropy that this module has no way to enforce, and operators do
choose their own keys.

HMAC with a server-side secret removes the assumption entirely: without the
secret there is nothing to test against, whatever the token looks like.

The secret is LLM_PROXY_IDENTITY_SECRET when set, because it is already
required for JWT signing and is stable across restarts — which matters, since
session_id doubles as the semantic cache's tenant key and a value that changed
on every boot would strand cached entries. When it is absent the fallback is a
per-process random: still unguessable, at the cost of cold cache and fresh
trajectory state after a restart, both of which are already per-process.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets as _secrets

logger = logging.getLogger("llmproxy.session_id")

#: Hex characters kept. Sixteen is 64 bits — ample to distinguish sessions,
#: short enough to read in a log line.
_LENGTH = 16

_fallback_secret: str | None = None


def _derivation_secret() -> bytes:
    global _fallback_secret

    from core.infisical import get_secret

    configured = get_secret("LLM_PROXY_IDENTITY_SECRET", required=False)
    if configured:
        return str(configured).encode("utf-8")

    if _fallback_secret is None:
        _fallback_secret = _secrets.token_urlsafe(32)
        logger.info(
            "LLM_PROXY_IDENTITY_SECRET is not set — deriving session ids with a "
            "per-process key. Session continuity and semantic-cache tenancy "
            "will reset on restart; set the variable to make them stable."
        )
    return _fallback_secret.encode("utf-8")


def from_token(token: str) -> str:
    """A stable, non-reversible identifier for the caller holding `token`."""
    return hmac.new(
        _derivation_secret(), token.encode("utf-8"), hashlib.sha256
    ).hexdigest()[:_LENGTH]


def from_fingerprint(ip: str, user_agent: str, accept_language: str) -> str:
    """Identifier for an unauthenticated caller.

    Used when auth is disabled, so that every client behind one NAT is not
    collapsed into a single session — which would make the multi-turn injection
    detector score unrelated callers together. These inputs are not secret, so
    the HMAC buys nothing here; it is used anyway so both branches produce
    identifiers from the same keyspace.
    """
    material = f"{ip}:{user_agent}:{accept_language}"
    return hmac.new(
        _derivation_secret(), material.encode("utf-8"), hashlib.sha256
    ).hexdigest()[:_LENGTH]


def reset_for_tests() -> None:
    """Forget the per-process fallback key."""
    global _fallback_secret
    _fallback_secret = None

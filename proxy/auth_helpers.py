"""LLMProxy — Authentication helpers.

Pure functions for API-key resolution and constant-time comparison.
Stateless — the orchestrator calls them with config + token; tests can
exercise them directly.

Extracted from proxy/rotator.py to make the timing-safe verifier
reusable and independently testable.
"""

from __future__ import annotations

import hmac
from typing import Any

from core.secrets import SecretManager


def resolve_api_keys(config: dict[str, Any]) -> list[str]:
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


def resolve_admin_keys(config: dict[str, Any]) -> list[str]:
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


def verify_admin_key(token: str, config: dict[str, Any]) -> bool:
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


def verify_api_key(token: str, valid_keys: list[str]) -> bool:
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


async def require_data_plane_auth(agent: Any, api_key: str | None) -> None:
    """Reject an unauthenticated caller on an OpenAI-compatible /v1/ route.

    The global middleware in proxy/app_factory.py denies /api/v1/ and /admin/
    by prefix, but deliberately does not cover /v1/: the data plane accepts a
    JWT as well as an API key, and the middleware only knows how to check the
    latter, so protecting /v1/ there would reject valid JWT callers.

    That left each /v1/ handler responsible for its own check, and /v1/models
    was written without one — so it served the configured provider and model
    inventory to anyone who could reach the port, while its siblings returned
    401. This helper is that missing check, shaped like the one chat,
    completions and embeddings already perform inline; those three predate it
    and could adopt it, which would remove three copies of this logic.

    Raises HTTPException(401) when auth is enabled and the caller has no valid
    credential. Returns silently when auth is disabled.
    """
    from fastapi import HTTPException

    from core.auth_policy import auth_enabled

    if not auth_enabled(agent.config):
        return

    if not api_key:
        raise HTTPException(status_code=401, detail="Unauthorized: Missing API key")

    token = parse_bearer(api_key)
    if not token:
        raise HTTPException(status_code=401, detail="Unauthorized: Empty token")

    identity = getattr(agent, "identity", None)
    if identity is not None and getattr(identity, "enabled", False):
        try:
            verified = identity.verify_proxy_jwt(token) or await identity.verify_token(
                token
            )
        except ValueError:
            verified = None
        if verified and getattr(verified, "verified", False):
            return

    if not agent._verify_api_key(token):
        raise HTTPException(status_code=401, detail="Unauthorized: Invalid API key or JWT")


async def resolve_control_plane_principal(agent: Any, token: str):
    """Who is making this control-plane call, and with which roles.

    Returns a `(kind, roles)` tuple, or None when the token authenticates as
    nobody. The middleware used to ask only "is this an admin API key?", which
    meant a verified SSO identity — and the admin_auth JWT the code implements
    — could not reach the control plane at all.

    Order matters. The API key is checked first because it is a constant-time
    string comparison against a small bag, while the JWT paths do signature
    verification and, for an external OIDC token, potentially a JWKS fetch.

    An API key resolves to the `admin` role deliberately: that is the authority
    it has had since the two tiers were introduced, and preserving it exactly is
    what makes adding role checks a no-op for every key-authenticated
    deployment.
    """
    if not token:
        return None

    if agent._verify_admin_key(token):
        return ("api_key", ["admin"])

    identity = getattr(agent, "identity", None)
    if identity is not None and getattr(identity, "enabled", False):
        try:
            verified = identity.verify_proxy_jwt(token)
            if verified is None:
                verified = await identity.verify_token(token)
        except ValueError:
            verified = None
        if verified is not None and getattr(verified, "verified", False):
            return ("jwt", list(getattr(verified, "roles", None) or []))

    # The enterprise admin-UI JWT: a separate, symmetric-key path that the
    # admin routes branch to when server.admin_auth.oidc_enabled is set. It was
    # unreachable for the same reason — the middleware rejected the token
    # before dispatch — so its required_role check never ran either.
    jwt_authenticator = getattr(agent, "jwt_authenticator", None)
    if jwt_authenticator is not None and getattr(jwt_authenticator, "enabled", False):
        try:
            if jwt_authenticator.verify_token(token):
                return ("admin_jwt", ["admin"])
        except Exception:  # noqa: BLE001 — a malformed token is just a refusal
            pass

    return None


def audit_principal(request: Any, token: str = "") -> str:
    """Who an audit or spend row is attributed to.

    The first eight characters of an API key, or the signed-in user for a
    caller admitted on an identity token. It used to be worked out per route
    from the bearer token alone, which gave every SSO user the same value (the
    first eight characters of any JWT are its header, ``eyJhbGci``) and gave
    streamed requests none at all: the stream is accounted for by the
    forwarder, which read a value nothing ever set. ``authenticate_data_plane``
    records it once and every writer reads it from here.
    """
    recorded = getattr(getattr(request, "state", None), "audit_principal", None)
    if isinstance(recorded, str):
        return recorded
    return (token[:8] + "...") if token else ""


def principal_already_verified(request: Any) -> bool:
    """True when the global middleware already authenticated this request.

    The per-route _check_admin_auth() closures are defence in depth: they exist
    to catch a gap in the middleware's prefix configuration. But they each
    re-verified the ADMIN KEY specifically, which meant a caller the middleware
    had just admitted on a JWT was refused one layer later — the same mismatch
    that made the log stream unreachable in a two-tier deployment, repeated
    across five route modules.

    Deferring to the middleware's verdict when it ran keeps the defence (a
    request that never passed the middleware still gets the full check) without
    the contradiction.
    """
    return getattr(getattr(request, "state", None), "principal_kind", None) is not None


async def authenticate_data_plane(
    agent: Any, request: Any, authorization: str | None, *, enforce_quota: bool = False
) -> str:
    """Authenticate a /v1/ caller; the one implementation behind every data-plane route.

    chat, completions and embeddings each carried their own copy of this block
    and the copies had drifted: only chat recorded the missing-key, empty-token
    and invalid-key failures, so a client brute-forcing keys against
    /v1/completions moved no llm_proxy_auth_failures_total counter, and only
    chat dispatched the AUTH_FAILURE webhook or verified the Tailscale identity.
    Everything now lives here, so a new credential type or a new failure reason
    is added once.

    ``authorization`` is the raw Authorization header value (a bearer token, or a bare
    key). Returns the bearer token ("" when authentication is disabled). Raises
    HTTPException(401/403) on failure. A valid key whose quota is exhausted sets
    ``request.state.quota_exceeded`` for the pipeline to enforce; a route that
    never reaches the pipeline passes ``enforce_quota=True`` and gets a 402.
    """
    import logging

    from fastapi import HTTPException

    from core.auth_policy import auth_enabled
    from core.metrics import MetricsTracker
    from core.webhooks import EventType

    logger = logging.getLogger("llmproxy.auth")

    if not auth_enabled(agent.config):
        request.state.audit_principal = ""
        return ""

    ip = request.client.host if request.client else "unknown"

    if not authorization:
        MetricsTracker.track_auth_failure("missing_key")
        agent._spawn_task(
            agent.webhooks.dispatch(
                EventType.AUTH_FAILURE, {"reason": "missing_key", "ip": ip}
            )
        )
        raise HTTPException(status_code=401, detail="Unauthorized: Missing API key")

    token = parse_bearer(authorization)
    if not token:
        MetricsTracker.track_auth_failure("empty_token")
        raise HTTPException(status_code=401, detail="Unauthorized: Empty token")

    identity = None
    if agent.identity.enabled:
        try:
            identity = agent.identity.verify_proxy_jwt(token)
            if not identity:
                identity = await agent.identity.verify_token(token)
        except ValueError as e:
            MetricsTracker.track_auth_failure("jwt_invalid")
            agent._spawn_task(
                agent.webhooks.dispatch(
                    EventType.AUTH_FAILURE, {"reason": "jwt_invalid", "error": str(e)}
                )
            )
            # H7: Don't leak internal error details (JWKS paths, OIDC URLs, JWT
            # algorithm info). Log the full error, return a generic one.
            logger.warning(f"Identity verification failed: {e}")
            raise HTTPException(
                status_code=401, detail="Unauthorized: Invalid or expired token"
            ) from e

    if identity and identity.verified:
        request.state.identity = identity
        request.state.user = identity.email or identity.subject
        request.state.roles = identity.roles
        if not agent.rbac.check_permission(identity.roles, "proxy:use"):
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        await agent.rbac.set_user_roles(identity.subject, identity.email, identity.roles)
        await agent._add_log(
            f"IDENTITY: {identity.provider} user={identity.email or identity.subject} roles={identity.roles}",
            level="SECURITY",
        )
    else:
        if not agent._verify_api_key(token):
            MetricsTracker.track_auth_failure("invalid_key")
            # Surface on the Security dashboard's live event feed: a rejected
            # key is exactly the signal an operator wants to see.
            await agent._add_log(
                f"AUTH: rejected invalid API key from {ip}", level="SECURITY"
            )
            agent._spawn_task(
                agent.webhooks.dispatch(
                    EventType.AUTH_FAILURE, {"reason": "invalid_api_key", "ip": ip}
                )
            )
            raise HTTPException(
                status_code=401, detail="Unauthorized: Invalid API key or JWT"
            )

        if not await agent.rbac.check_quota(token):
            agent._spawn_task(
                agent.webhooks.dispatch(
                    EventType.BUDGET_THRESHOLD,
                    {"reason": "quota_exceeded", "key_prefix": token[:8] + "..."},
                )
            )
            request.state.quota_exceeded = True
            if enforce_quota:
                raise HTTPException(
                    status_code=402,
                    detail="FinOps: Budget Exceeded (HTTP 402). "
                    "API key quota exhausted.",
                )

    client_host = request.client.host if request.client else "0.0.0.0"  # nosec B104
    ts_id = await agent.zt_manager.verify_tailscale_identity(client_host)
    if ts_id["status"] == "verified":
        await agent._add_log(
            f"ZT VERIFIED: {ts_id['user']} on {ts_id['node']}", level="SECURITY"
        )
        request.state.user = getattr(request.state, "user", None) or ts_id["user"]
        request.state.node = ts_id["node"]

    if identity and identity.verified:
        request.state.audit_principal = str(identity.email or identity.subject or "")
    else:
        request.state.audit_principal = token[:8] + "..."
    return token

"""
LLMPROXY — Identity & SSO Module (Session 6)

Stateless OIDC/JWT identity verification with multi-provider support.
No user database — identity is derived from cryptographic tokens.

Supported flows:
  1. OIDC JWT (Google, Microsoft, Apple) — validated via JWKS
  2. Tailscale identity fallback — via LocalAPI socket (see zero_trust.py)
  3. API key fallback — existing key-based auth

Architecture:
  - Middleware intercepts requests, extracts Bearer token
  - If token is a JWT, validate signature via cached JWKS
  - Extract claims (sub, email, roles) → attach to request.state
  - RBAC integration maps JWT claims to internal roles
"""

import asyncio
import concurrent.futures
import time
import logging
import aiohttp
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field

import jwt
from jwt import PyJWKClient, InvalidTokenError

from core.infisical import get_secret

logger = logging.getLogger(__name__)

# JWKS cache TTL (seconds)
JWKS_CACHE_TTL = 3600

# How long a JWKS fetch may take before it is abandoned. PyJWT's own default is
# 30 s; the fetch runs off the event loop (verify_token hands it to a thread)
# but the caller still waits, so this bounds how long one request can hang on a
# stalled identity provider.
JWKS_FETCH_TIMEOUT_S = 5

# Total budget one request may spend obtaining a signing key: the wait for
# another request's in-flight fetch, plus its own. Bounded because the
# coalescing lock below turns concurrent misses into a queue, and a queue with
# no ceiling in front of a stalled provider is the hang the fetch timeout was
# added to prevent, moved one level out.
JWKS_TOTAL_BUDGET_S = 2 * JWKS_FETCH_TIMEOUT_S

# JWKS fetches run on their own small pool, not on the loop's default executor.
#
# asyncio.to_thread() submits to the default executor — min(32, cpu_count + 4)
# threads — which this process also uses for the event-log DLQ writes, the
# config-file hashing in the watcher, the semantic-cache lookups and the
# security shield's regex scans. Handing a synchronous urllib fetch against a
# third party to that pool means a stalled identity provider occupies threads
# that unrelated blocking work needs: the fix for "one slow IdP stalls the
# event loop" became "one slow IdP starves everything that offloads".
#
# Four workers, because with the coalescing lock at most one fetch per provider
# is ever in flight, and core/wasm_runner.py sets the same precedent for the
# same reason.
_JWKS_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
    max_workers=4, thread_name_prefix="jwks"
)


def shutdown_jwks_executor(wait: bool = False) -> None:
    """Release the JWKS fetch threads on shutdown."""
    _JWKS_EXECUTOR.shutdown(wait=wait, cancel_futures=True)


@dataclass
class IdentityContext:
    """Represents a verified user identity attached to a request."""

    provider: str  # "google", "microsoft", "apple", "tailscale", "api_key"
    subject: str  # Unique user ID (sub claim or API key hash)
    email: Optional[str] = None
    name: Optional[str] = None
    roles: List[str] = field(default_factory=lambda: ["user"])
    raw_claims: Dict[str, Any] = field(default_factory=dict)
    verified: bool = False


@dataclass
class OIDCProvider:
    """Configuration for a single OIDC identity provider."""

    name: str
    issuer: str
    jwks_uri: str
    client_id: str
    audience: Optional[str] = None
    # Claim mapping
    email_claim: str = "email"
    name_claim: str = "name"
    roles_claim: str = "roles"


# Well-known OIDC discovery endpoints
WELL_KNOWN_PROVIDERS = {
    "google": {
        "issuer": "https://accounts.google.com",
        "discovery": "https://accounts.google.com/.well-known/openid-configuration",
    },
    "microsoft": {
        "issuer": "https://login.microsoftonline.com/common/v2.0",
        "discovery": "https://login.microsoftonline.com/common/v2.0/.well-known/openid-configuration",
    },
    "apple": {
        "issuer": "https://appleid.apple.com",
        "discovery": "https://appleid.apple.com/.well-known/openid-configuration",
    },
}


class IdentityManager:
    """
    Stateless OIDC JWT validation with multi-provider JWKS support.
    No user database — identity is derived entirely from token claims.
    """

    def __init__(self, config: Dict[str, Any]):
        identity_cfg = config.get("identity", {})
        self.enabled = identity_cfg.get("enabled", False)
        self.providers: Dict[str, OIDCProvider] = {}
        self._jwks_clients: Dict[str, PyJWKClient] = {}
        self._jwks_cache_ts: Dict[str, float] = {}
        # One lock per provider, so concurrent cache misses coalesce into a
        # single fetch instead of a thundering herd of identical ones.
        self._jwks_locks: Dict[str, asyncio.Lock] = {}
        self._session: Optional[aiohttp.ClientSession] = None

        # Default role for authenticated users
        self.default_role = identity_cfg.get("default_role", "user")
        # Claims-to-roles mapping (e.g., {"admin@example.com": ["admin"]})
        self.role_mappings = identity_cfg.get("role_mappings", {})

        if self.enabled:
            self._load_providers(identity_cfg.get("providers", []))

    def _load_providers(self, provider_configs: List[Dict[str, Any]]):
        """Load OIDC providers from config."""
        for pcfg in provider_configs:
            name = pcfg.get("name", "").lower()
            # Resolve client_id from Infisical/env
            client_id_key = pcfg.get("client_id_env", f"OIDC_{name.upper()}_CLIENT_ID")
            client_id = get_secret(client_id_key, required=False) or pcfg.get(
                "client_id", ""
            )

            # Use well-known defaults or explicit config
            well_known = WELL_KNOWN_PROVIDERS.get(name, {})
            issuer = pcfg.get("issuer", well_known.get("issuer", ""))
            jwks_uri = pcfg.get("jwks_uri", "")

            if not jwks_uri and issuer:
                # Derive JWKS URI from issuer (standard OIDC pattern)
                jwks_uri = f"{issuer.rstrip('/')}/.well-known/jwks.json"
                # Google uses a different path
                if name == "google":
                    jwks_uri = "https://www.googleapis.com/oauth2/v3/certs"
                elif name == "microsoft":
                    jwks_uri = (
                        "https://login.microsoftonline.com/common/discovery/v2.0/keys"
                    )
                elif name == "apple":
                    jwks_uri = "https://appleid.apple.com/auth/keys"

            if not issuer or not client_id:
                logger.warning(
                    f"Identity: Provider '{name}' skipped — missing issuer or client_id"
                )
                continue

            provider = OIDCProvider(
                name=name,
                issuer=issuer,
                jwks_uri=jwks_uri,
                client_id=client_id,
                audience=pcfg.get("audience", client_id),
                email_claim=pcfg.get("email_claim", "email"),
                name_claim=pcfg.get("name_claim", "name"),
                roles_claim=pcfg.get("roles_claim", "roles"),
            )
            self.providers[name] = provider
            logger.info(f"Identity: Loaded OIDC provider '{name}' (issuer={issuer})")

    def _get_jwks_client(self, provider: OIDCProvider) -> PyJWKClient:
        """Returns a cached PyJWKClient for the provider, refreshing if stale."""
        now = time.time()
        cached_ts = self._jwks_cache_ts.get(provider.name, 0)

        if (
            provider.name not in self._jwks_clients
            or (now - cached_ts) > JWKS_CACHE_TTL
        ):
            self._jwks_clients[provider.name] = PyJWKClient(
                provider.jwks_uri,
                cache_keys=True,
                lifespan=JWKS_CACHE_TTL,
                # PyJWT defaults this to 30 seconds. The fetch is synchronous
                # urllib on the event loop (see verify_token), so that default
                # is thirty seconds of every in-flight request stalling, not
                # just this one. Bound it to something a request path can wear.
                timeout=JWKS_FETCH_TIMEOUT_S,
            )
            self._jwks_cache_ts[provider.name] = now

        return self._jwks_clients[provider.name]

    async def _signing_key(self, provider: OIDCProvider, token: str):
        """Resolve the signing key for `token`, off the event loop.

        get_signing_key_from_jwt fetches over synchronous urllib on a cache
        miss — at least once per provider per JWKS_CACHE_TTL, and again for any
        kid the cached set does not contain. Called directly it blocked the
        single event loop for the whole fetch, so a slow identity provider
        stalled every in-flight request rather than only the one
        authenticating.

        Moving it to a thread fixed that but left two things:

          * asyncio.to_thread submits to the loop's DEFAULT executor, shared
            with every other blocking offload in the process, so a stalled
            provider starved them instead;
          * nothing coalesced concurrent misses, so N simultaneous logins after
            a restart or a key rotation fired N identical fetches, each holding
            a thread for up to JWKS_FETCH_TIMEOUT_S.

        So: a dedicated pool, one fetch per provider at a time, and a ceiling
        on how long any one request waits for the result.
        """
        lock = self._jwks_locks.setdefault(provider.name, asyncio.Lock())
        loop = asyncio.get_running_loop()

        async def _resolve():
            async with lock:
                # Re-read the client inside the lock: the fetch we queued
                # behind may have refreshed a stale one.
                jwks_client = self._get_jwks_client(provider)
                return await loop.run_in_executor(
                    _JWKS_EXECUTOR, jwks_client.get_signing_key_from_jwt, token
                )

        try:
            return await asyncio.wait_for(_resolve(), timeout=JWKS_TOTAL_BUDGET_S)
        except asyncio.TimeoutError:
            # Fail closed. The abandoned fetch keeps running on its thread and
            # populates the client cache, so the next caller is likely to be
            # served from it rather than repeating this wait.
            logger.warning(
                "Identity: JWKS key lookup for provider '%s' exceeded %ss; "
                "refusing the token rather than holding the request.",
                provider.name,
                JWKS_TOTAL_BUDGET_S,
            )
            raise ValueError("Identity provider unavailable")

    async def verify_token(self, token: str) -> Optional[IdentityContext]:
        """
        Verify a JWT token against all configured OIDC providers.
        Returns an IdentityContext if valid, None if not a recognized JWT.
        Raises ValueError on invalid/expired tokens.
        """
        if not self.enabled or not self.providers:
            return None

        # Quick check: is this a JWT? (3 dot-separated segments)
        parts = token.split(".")
        if len(parts) != 3:
            return None  # Not a JWT, let API key auth handle it

        # Try to decode header to find issuer hint
        try:
            unverified = jwt.decode(token, options={"verify_signature": False})
            issuer = unverified.get("iss", "")
        except Exception:
            return None  # Malformed JWT

        # Find matching provider
        provider = None
        for p in self.providers.values():
            if p.issuer == issuer:
                provider = p
                break

        if not provider:
            logger.debug(f"Identity: No provider matches issuer '{issuer}'")
            return None

        # Validate signature via JWKS
        try:
            signing_key = await self._signing_key(provider, token)

            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256", "ES256"],
                audience=provider.audience,
                issuer=provider.issuer,
                options={
                    "verify_exp": True,
                    "verify_iat": True,
                    "verify_aud": True,
                    "verify_iss": True,
                },
            )
        except jwt.ExpiredSignatureError:
            raise ValueError("Token expired")
        except jwt.InvalidAudienceError:
            raise ValueError("Invalid audience")
        except InvalidTokenError as e:
            raise ValueError(f"Invalid token: {e}")

        # Extract identity from claims
        email = claims.get(provider.email_claim)
        name = claims.get(provider.name_claim)
        subject = claims.get("sub", "unknown")

        # Resolve roles
        roles = self._resolve_roles(claims, provider, email)

        identity = IdentityContext(
            provider=provider.name,
            subject=subject,
            email=email,
            name=name,
            roles=roles,
            raw_claims=claims,
            verified=True,
        )

        logger.info(
            f"Identity: Verified {provider.name} user={email or subject} roles={roles}"
        )
        return identity

    def _resolve_roles(
        self, claims: Dict[str, Any], provider: OIDCProvider, email: Optional[str]
    ) -> List[str]:
        """
        Map JWT claims to internal RBAC roles.

        Priority:
          1. Explicit role_mappings from config (email → roles)
          2. Roles claim from JWT (e.g., Azure AD groups)
          3. Default role
        """
        # Check config-based email → role mapping
        if email and email in self.role_mappings:
            return list(self.role_mappings[email])

        # Check JWT roles claim (e.g., Azure AD `roles` or `groups`)
        # R2-02: Validate against known RBAC roles to prevent role injection
        # from attacker-controlled OIDC tenants.
        jwt_roles = claims.get(provider.roles_claim)
        if isinstance(jwt_roles, list) and jwt_roles:
            from core.rbac import DEFAULT_PERMISSIONS

            valid_roles = set(DEFAULT_PERMISSIONS.keys())
            filtered = [r for r in jwt_roles if isinstance(r, str) and r in valid_roles]
            return filtered if filtered else [self.default_role]

        return [self.default_role]

    def generate_proxy_jwt(self, identity: IdentityContext, ttl: int = 3600) -> str:
        """
        Generate a short-lived internal JWT for downstream services.
        Used for session management after initial OIDC verification.
        """
        secret = get_secret("LLM_PROXY_IDENTITY_SECRET", required=True)
        if secret is None:
            raise ValueError("LLM_PROXY_IDENTITY_SECRET is required but not set")
        payload = {
            "iss": "llmproxy",
            "sub": identity.subject,
            "email": identity.email,
            "name": identity.name,
            "roles": identity.roles,
            "provider": identity.provider,
            "iat": int(time.time()),
            "exp": int(time.time()) + ttl,
        }
        return jwt.encode(payload, secret, algorithm="HS256")

    def verify_proxy_jwt(self, token: str) -> Optional[IdentityContext]:
        """
        Verify an internal proxy JWT (issued by generate_proxy_jwt).
        Used for session continuity — avoids re-validating external OIDC on every request.
        """
        secret = get_secret("LLM_PROXY_IDENTITY_SECRET", required=False)
        if not secret:
            return None
        try:
            claims = jwt.decode(
                token,
                secret,
                algorithms=["HS256"],
                issuer="llmproxy",
                options={"verify_exp": True},
            )
            return IdentityContext(
                provider=claims.get("provider", "proxy"),
                subject=claims.get("sub", "unknown"),
                email=claims.get("email"),
                name=claims.get("name"),
                roles=claims.get("roles", [self.default_role]),
                raw_claims=claims,
                verified=True,
            )
        except InvalidTokenError:
            return None

    async def close(self):
        """Cleanup resources."""
        if self._session and not self._session.closed:
            await self._session.close()

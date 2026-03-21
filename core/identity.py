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


@dataclass
class IdentityContext:
    """Represents a verified user identity attached to a request."""
    provider: str           # "google", "microsoft", "apple", "tailscale", "api_key"
    subject: str            # Unique user ID (sub claim or API key hash)
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
            client_id = get_secret(client_id_key, required=False) or pcfg.get("client_id", "")

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
                    jwks_uri = "https://login.microsoftonline.com/common/discovery/v2.0/keys"
                elif name == "apple":
                    jwks_uri = "https://appleid.apple.com/auth/keys"

            if not issuer or not client_id:
                logger.warning(f"Identity: Provider '{name}' skipped — missing issuer or client_id")
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

        if provider.name not in self._jwks_clients or (now - cached_ts) > JWKS_CACHE_TTL:
            self._jwks_clients[provider.name] = PyJWKClient(
                provider.jwks_uri,
                cache_keys=True,
                lifespan=JWKS_CACHE_TTL,
            )
            self._jwks_cache_ts[provider.name] = now

        return self._jwks_clients[provider.name]

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
            if p.issuer == issuer or issuer.startswith(p.issuer.rstrip("/")):
                provider = p
                break

        if not provider:
            logger.debug(f"Identity: No provider matches issuer '{issuer}'")
            return None

        # Validate signature via JWKS
        try:
            jwks_client = self._get_jwks_client(provider)
            signing_key = jwks_client.get_signing_key_from_jwt(token)

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

        logger.info(f"Identity: Verified {provider.name} user={email or subject} roles={roles}")
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
            return self.role_mappings[email]

        # Check JWT roles claim (e.g., Azure AD `roles` or `groups`)
        jwt_roles = claims.get(provider.roles_claim)
        if isinstance(jwt_roles, list) and jwt_roles:
            return jwt_roles

        return [self.default_role]

    def generate_proxy_jwt(self, identity: IdentityContext, ttl: int = 3600) -> str:
        """
        Generate a short-lived internal JWT for downstream services.
        Used for session management after initial OIDC verification.
        """
        secret = get_secret("LLM_PROXY_IDENTITY_SECRET", required=True)
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
                token, secret, algorithms=["HS256"],
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

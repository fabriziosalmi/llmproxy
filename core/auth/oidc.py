import jwt
import logging
from typing import Optional, Dict, Any
from fastapi import Request

logger = logging.getLogger(__name__)

class JWTAuthenticator:
    """
    Validates JWT tokens for the Admin UI, replacing static API keys for enterprise deployments.
    Supports symmetric (HS256) or asymmetric (RS256) validation via PyJWT.
    """
    def __init__(self, config: Dict[str, Any]):
        auth_config = config.get("server", {}).get("admin_auth", {})
        # If explicitly enabled, it will override static API keys
        self.enabled = auth_config.get("oidc_enabled", False)

        # In a full IdP setup (Auth0, Okta, Keycloak), this could be a JWKS client.
        # For this integration, we support a provided secret/public key.
        #
        # No default. This previously fell back to a constant published in this
        # repository, so enabling oidc_enabled without setting jwt_secret meant
        # admin tokens were validated against a value anyone could read — and
        # anyone could therefore mint one. Fail closed at construction instead:
        # startup aborts rather than coming up insecure.
        self.secret = auth_config.get("jwt_secret")
        if self.enabled and not self.secret:
            raise ValueError(
                "server.admin_auth.oidc_enabled is true but jwt_secret is not set. "
                "Refusing to start: admin JWTs would be unverifiable. "
                "Set server.admin_auth.jwt_secret (or disable oidc_enabled)."
            )
        self.algorithms = [auth_config.get("jwt_algorithm", "HS256")]
        self.audience = auth_config.get("jwt_audience", None)
        self.issuer = auth_config.get("jwt_issuer", None)
        # Optional RBAC: when set, the JWT must carry this role in its `roles`
        # claim (list or space/comma-separated string) to be accepted for admin
        # access. Unset (default) → any validly-signed token is accepted
        # (back-compat). The claim key is configurable for non-standard IdPs.
        self.required_role = auth_config.get("required_role", None)
        self.roles_claim = auth_config.get("roles_claim", "roles")
        if self.enabled and not self.required_role:
            # Back-compat default, but a loud one: without a required role every
            # validly-signed token from the issuer is an admin token, which in a
            # real IdP deployment means the whole directory.
            logger.warning(
                "admin_auth: oidc_enabled without required_role — ANY validly-signed "
                "token is accepted for admin access. Set server.admin_auth.required_role."
            )

    def verify_token(self, token: str) -> bool:
        if not self.enabled:
            # If OIDC is not enabled, we fallback to the proxy's API key mechanism
            return False

        try:
            payload = jwt.decode(
                token,
                self.secret,
                algorithms=self.algorithms,
                audience=self.audience,
                issuer=self.issuer
            )
            # RBAC: if a required role is configured, the token must carry it.
            if self.required_role:
                raw = payload.get(self.roles_claim, [])
                if isinstance(raw, str):
                    roles = raw.replace(",", " ").split()
                elif isinstance(raw, (list, tuple)):
                    roles = [str(r) for r in raw]
                else:
                    roles = []
                if self.required_role not in roles:
                    logger.warning(
                        "Admin UI auth failed: JWT for subject %s lacks required "
                        "role %r (has %s)",
                        payload.get("sub", "unknown"), self.required_role, roles,
                    )
                    return False
            logger.debug(f"JWT validated successfully for subject: {payload.get('sub', 'unknown')}")
            return True
        except jwt.ExpiredSignatureError:
            logger.warning("Admin UI auth failed: JWT expired")
            return False
        except jwt.InvalidTokenError as e:
            logger.warning(f"Admin UI auth failed: Invalid JWT ({e})")
            return False

    def get_token_from_request(self, request: Request) -> Optional[str]:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.lower().startswith("bearer "):
            return auth_header[7:].strip()
        return None

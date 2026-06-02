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
        self.secret = auth_config.get("jwt_secret", "fallback-dev-secret-do-not-use")
        self.algorithms = [auth_config.get("jwt_algorithm", "HS256")]
        self.audience = auth_config.get("jwt_audience", None)
        self.issuer = auth_config.get("jwt_issuer", None)

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
            # Future RBAC enhancement: verify payload.get("roles") contains "admin"
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

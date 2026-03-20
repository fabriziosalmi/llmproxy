import ssl
import logging
import jwt
import time
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

class ZeroTrustManager:
    """Manages mTLS and Identity headers for Zero-Trust upstream communication."""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config.get("security", {}).get("zero_trust", {})
        self.enabled = self.config.get("enabled", False)
        self.secret = self.config.get("identity_secret", "proxy-secret-123")
        self.cert_path = self.config.get("client_cert")
        self.key_path = self.config.get("client_key")

    def get_ssl_context(self) -> Optional[ssl.SSLContext]:
        """Returns an SSLContext for mTLS if configured."""
        if not self.enabled or not self.cert_path:
            return None
            
        try:
            context = ssl.create_default_context()
            context.load_cert_chain(certfile=self.cert_path, keyfile=self.key_path)
            return context
        except Exception as e:
            logger.error(f"ZeroTrust: Failed to load mTLS certificates: {e}")
            return None

    def get_identity_headers(self) -> Dict[str, str]:
        """Generates identity headers (e.g., JWT) for upstream identification."""
        if not self.enabled:
            return {}
            
        # Generate a short-lived JWT for the proxy identity
        payload = {
            "iss": "llmproxy",
            "iat": int(time.time()),
            "exp": int(time.time()) + 60, # 1 minute expiry
            "role": "trusted-aggregator"
        }
        
        token = jwt.encode(payload, self.secret, algorithm="HS256")
        return {
            "X-Proxy-Identity": token,
            "X-Zero-Trust": "true"
        }

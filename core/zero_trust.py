import ssl
import logging
import jwt
import time
import os
import aiohttp
from typing import Dict, Any, Optional

from core.infisical import get_secret

# 11.5: Tailscale Unix Socket Paths
TAILSCALE_SOCKET_LINUX = "/var/run/tailscale/tailscaled.sock"
TAILSCALE_SOCKET_MACOS = "/Library/Tailscale/tailscaled.sock"

logger = logging.getLogger(__name__)

class ZeroTrustManager:
    """Manages mTLS and Identity headers for Zero-Trust upstream communication."""

    def __init__(self, config: Dict[str, Any]):
        self.config = config.get("security", {}).get("zero_trust", {})
        self.enabled = self.config.get("enabled", False)
        self.secret = get_secret("LLM_PROXY_IDENTITY_SECRET", required=self.enabled)
        self.cert_path = self.config.get("client_cert")
        self.key_path = self.config.get("client_key")
        
        # Tailscale Socket Configuration
        self.ts_socket = TAILSCALE_SOCKET_MACOS if os.path.exists(TAILSCALE_SOCKET_MACOS) else TAILSCALE_SOCKET_LINUX
        self._ts_session: Optional[aiohttp.ClientSession] = None

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

    async def verify_tailscale_identity(self, remote_ip: str) -> Optional[Dict[str, Any]]:
        """
        11.5: Queries Tailscale LocalAPI via Unix Socket to verify the machine/user associated with the IP.
        This provides a zero-latency, spoof-proof identity check for the Federated Swarm.
        """
        if not os.path.exists(self.ts_socket):
            return {"status": "unverified", "reason": "socket_not_found"}

        try:
            if not self._ts_session or self._ts_session.closed:
                connector = aiohttp.UnixConnector(path=self.ts_socket)
                self._ts_session = aiohttp.ClientSession(connector=connector)

            # Query the LocalAPI for who is at this remote IP
            async with self._ts_session.get(f"http://local-tailscale/localapi/v0/whois?addr={remote_ip}") as resp:
                if resp.status == 200:
                    data = await resp.json()
                    user = data.get("UserProfile", {}).get("LoginName", "unknown")
                    node = data.get("Node", {}).get("Name", "unknown")
                    logger.info(f"ZeroTrust: Verified Tailscale User {user} at Node {node}")
                    return {
                        "status": "verified",
                        "user": user,
                        "node": node,
                        "caps": data.get("CapMap", {})
                    }
        except Exception as e:
            logger.error(f"ZeroTrust: Tailscale Socket Error: {e}")
        
        return {"status": "unverified", "reason": "api_error"}

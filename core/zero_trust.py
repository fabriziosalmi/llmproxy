import asyncio
import ssl
import logging
import jwt
import time
import os
import aiohttp
from urllib.parse import quote
from typing import Dict, Any, Optional

from core.infisical import get_secret

# 11.5: Tailscale Unix Socket Paths
TAILSCALE_SOCKET_LINUX = "/var/run/tailscale/tailscaled.sock"
TAILSCALE_SOCKET_MACOS = "/Library/Tailscale/tailscaled.sock"

logger = logging.getLogger(__name__)

# How long the Tailscale LocalAPI may take before the lookup is abandoned.
#
# This is a request over a unix socket to a daemon on the same host: a round
# trip that takes longer than a second is not slow, it is broken. The session
# used to be built with no timeout at all, which meant aiohttp's 300-second
# default applied — on the chat request path, guarded only by the socket FILE
# existing, which says nothing about whether tailscaled is answering. A wedged
# or restarting daemon therefore held every request for up to five minutes,
# and the `except Exception` below could not help because a hang raises
# nothing. Same failure class as the Redis and JWKS timeouts added in 1.34.0;
# this call was missed by that sweep.
TAILSCALE_API_TIMEOUT_S = 1.0


class ZeroTrustManager:
    """Manages mTLS and Identity headers for Zero-Trust upstream communication."""

    def __init__(self, config: Dict[str, Any]):
        self.config = config.get("security", {}).get("zero_trust", {})
        self.enabled = self.config.get("enabled", False)
        self.secret = get_secret("LLM_PROXY_IDENTITY_SECRET", required=self.enabled)
        self.cert_path = self.config.get("client_cert")
        self.key_path = self.config.get("client_key")

        # Tailscale Socket Configuration
        self.ts_socket = (
            TAILSCALE_SOCKET_MACOS
            if os.path.exists(TAILSCALE_SOCKET_MACOS)
            else TAILSCALE_SOCKET_LINUX
        )
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
            "exp": int(time.time()) + 60,  # 1 minute expiry
            "role": "trusted-aggregator",
        }

        if self.secret is None:
            raise ValueError("Zero-trust secret not configured")
        token = jwt.encode(payload, self.secret, algorithm="HS256")
        return {"X-Proxy-Identity": token, "X-Zero-Trust": "true"}

    async def verify_tailscale_identity(
        self, remote_ip: str
    ) -> Optional[Dict[str, Any]]:
        """
        11.5: Queries Tailscale LocalAPI via Unix Socket to verify the machine/user associated with the IP.
        This provides a zero-latency, spoof-proof identity check for the Federated Swarm.
        """
        if not os.path.exists(self.ts_socket):
            return {"status": "unverified", "reason": "socket_not_found"}

        try:
            if not self._ts_session or self._ts_session.closed:
                connector = aiohttp.UnixConnector(path=self.ts_socket)
                self._ts_session = aiohttp.ClientSession(
                    connector=connector,
                    timeout=aiohttp.ClientTimeout(total=TAILSCALE_API_TIMEOUT_S),
                )

            # Query the LocalAPI for who is at this remote IP
            # Validate IP format before interpolating into URL
            safe_ip = quote(remote_ip, safe=".:[]")
            async with self._ts_session.get(
                f"http://local-tailscale/localapi/v0/whois?addr={safe_ip}"
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    user = data.get("UserProfile", {}).get("LoginName", "unknown")
                    node = data.get("Node", {}).get("Name", "unknown")
                    logger.info(
                        f"ZeroTrust: Verified Tailscale User {user} at Node {node}"
                    )
                    return {
                        "status": "verified",
                        "user": user,
                        "node": node,
                        "caps": data.get("CapMap", {}),
                    }
        except asyncio.TimeoutError:
            # Distinguished from a socket error on purpose: "the daemon did not
            # answer in time" and "the daemon refused" are different operational
            # problems, and the first one used to be invisible because it could
            # not occur — the request simply waited.
            logger.warning(
                "ZeroTrust: Tailscale LocalAPI did not answer within %.1fs — "
                "treating the caller as unverified",
                TAILSCALE_API_TIMEOUT_S,
            )
            return {"status": "unverified", "reason": "api_timeout"}
        except Exception as e:
            logger.error(f"ZeroTrust: Tailscale Socket Error: {e}")

        return {"status": "unverified", "reason": "api_error"}

    async def close(self) -> None:
        """Close internal HTTP resources."""
        if self._ts_session and not self._ts_session.closed:
            await self._ts_session.close()

import aiohttp
import logging
import random
from typing import List, Dict, Any, Optional

from core.infisical import get_secret

logger = logging.getLogger(__name__)

class FederationManager:
    """Manages peer-to-peer connectivity between LLMProxy instances."""

    def __init__(self, config: Dict[str, Any]):
        self.config = config.get("federation", {})
        self.enabled = self.config.get("enabled", False)
        self.peers = self.config.get("peers", [])
        self.trust_secret = get_secret("LLM_PROXY_FEDERATION_SECRET", required=self.enabled)

    async def discover_peers(self) -> List[str]:
        """Simulates Tailscale-based peer discovery for the Swarm fallback."""
        if not self.enabled: return []
        
        # In a real implementation, this would call 'tailscale status --json'
        # or use the Tailscale API to find other llmproxy nodes.
        active_peers = []
        for p in self.peers:
            if await self._check_health(p):
                active_peers.append(p)
        return active_peers

    async def _check_health(self, peer_url: str) -> bool:
        """Checks if a peer is alive and accepting federation requests."""
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(f"{peer_url}/health", timeout=2) as resp:
                    return resp.status == 200
            except Exception:
                return False

    async def forward_to_peer(self, peer_url: str, body: Dict[str, Any], identity_headers: Dict[str, str]) -> Optional[Dict[str, Any]]:
        """Forwards a request to a peer proxy with federation security."""
        logger.warning(f"Federation Swarm: Offloading request to peer {peer_url}")
        
        async with aiohttp.ClientSession() as session:
            try:
                headers = identity_headers.copy()
                headers["X-Federation-Secret"] = self.trust_secret
                headers["X-Swarm-Node"] = "origin-proxy-alpha"
                
                async with session.post(
                    f"{peer_url}/v1/chat/completions",
                    json=body,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=45)
                ) as response:
                    if response.status == 200:
                        return await response.json()
                    return None
            except Exception as e:
                logger.error(f"Federation Swarm: Peer {peer_url} failed: {e}")
                return None

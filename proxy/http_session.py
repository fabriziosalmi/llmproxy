"""LLMProxy — HTTP session factory.

Builds the aiohttp.ClientSession used for upstream requests, configured
from the proxy's `server` and `connection_pool` config sections. The
orchestrator owns the session lifecycle (caching, lock, close); this
module owns construction details only.

Extracted from proxy/rotator.py.
"""

from __future__ import annotations

from typing import Any, Dict

import aiohttp


def build_http_session(config: Dict[str, Any]) -> aiohttp.ClientSession:
    """Construct a fresh aiohttp.ClientSession from the proxy config.

    Reads `server.timeout` (default 30s) and `connection_pool.*` for pool
    sizing, DNS cache TTL, keepalive, and per-host limits. Connector has
    `enable_cleanup_closed=True` so dead connections don't pile up.

    Caller is responsible for caching/closing the returned session.
    """
    http_cfg = config.get("server", {})
    timeout_s = int(str(http_cfg.get("timeout", "30s")).rstrip("s"))
    pool_cfg = config.get("connection_pool", {})
    connector = aiohttp.TCPConnector(
        limit=pool_cfg.get("max_connections", 100),
        limit_per_host=pool_cfg.get("max_per_host", 30),
        ttl_dns_cache=pool_cfg.get("dns_cache_ttl", 300),
        enable_cleanup_closed=True,
        keepalive_timeout=pool_cfg.get("keepalive_timeout", 30),
    )
    return aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(
            total=timeout_s,
            sock_connect=pool_cfg.get("connect_timeout", 10),
            sock_read=timeout_s,
        ),
        connector=connector,
    )

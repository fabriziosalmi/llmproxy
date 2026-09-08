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

    Reads `server.timeout` (default 30s, applied as sock_read),
    `server.total_timeout` (optional overall ceiling, default none) and
    `connection_pool.*` for pool
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
    # `total` bounds the WHOLE operation, including reading the response body,
    # so a single value shared with sock_read truncates any completion whose
    # generation runs longer than it — routine for long or reasoning-heavy
    # output. Worse, the forwarder classifies the resulting timeout as
    # retryable, so the same request is re-sent to the next provider and the
    # caller waits twice for a second truncation while two providers bill.
    #
    # sock_read is the bound that matters: a stream that has stopped producing
    # tokens still fails within timeout_s. `total` therefore defaults to None
    # (no overall ceiling) and is opt-in via server.total_timeout for operators
    # who want one.
    total_timeout = http_cfg.get("total_timeout")
    return aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(
            total=int(str(total_timeout).rstrip("s")) if total_timeout else None,
            sock_connect=pool_cfg.get("connect_timeout", 10),
            sock_read=timeout_s,
        ),
        connector=connector,
    )

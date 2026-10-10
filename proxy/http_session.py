"""LLMProxy — HTTP session factory.

Builds the aiohttp.ClientSession used for upstream requests, configured
from the proxy's `server` and `connection_pool` config sections. The
orchestrator owns the session lifecycle (caching, lock, close); this
module owns construction details only.

Extracted from proxy/rotator.py.
"""

from __future__ import annotations

import weakref
from typing import Any

import aiohttp

#: Default wait for a non-streaming upstream response (server.response_timeout).
DEFAULT_RESPONSE_TIMEOUT_S = 600

#: The per-request timeout a session's non-streaming calls use. Kept beside the
#: session rather than on it (aiohttp discourages custom attributes), and weak
#: so a closed session takes its entry with it.
_RESPONSE_TIMEOUTS: weakref.WeakKeyDictionary[Any, aiohttp.ClientTimeout] = (
    weakref.WeakKeyDictionary()
)


def _seconds(value: Any, default: int) -> int:
    try:
        return int(str(value).rstrip("s"))
    except (TypeError, ValueError):
        return default


def response_timeout(session: Any) -> dict[str, Any]:
    """``timeout=`` for a non-streaming request on ``session``, as keyword arguments.

    Empty for a session this module did not build (a test double), so the call
    is made exactly as before.
    """
    try:
        timeout = _RESPONSE_TIMEOUTS.get(session)
    except TypeError:
        return {}
    return {"timeout": timeout} if timeout is not None else {}


def build_http_session(config: dict[str, Any]) -> aiohttp.ClientSession:
    """Construct a fresh aiohttp.ClientSession from the proxy config.

    Reads `server.timeout` (default 30s, applied as sock_read: the longest
    silence allowed on a stream), `server.response_timeout` (default 600s: how
    long a non-streaming response may take, see below),
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
    total = int(str(total_timeout).rstrip("s")) if total_timeout else None
    connect = pool_cfg.get("connect_timeout", 10)
    session = aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=total, sock_connect=connect, sock_read=timeout_s),
        connector=connector,
    )
    # A non-streaming upstream sends nothing until the whole answer exists, so
    # for it sock_read is not "the stream went quiet": it is a ceiling on the
    # generation, and removing `total` above did not remove that ceiling. At
    # the 30 s default a long or reasoning-heavy completion was cut and, the
    # timeout being retryable, sent again to the next provider. Non-streaming
    # calls get their own, longer bound (see response_timeout()).
    _RESPONSE_TIMEOUTS[session] = aiohttp.ClientTimeout(
        total=total,
        sock_connect=connect,
        sock_read=_seconds(http_cfg.get("response_timeout"), DEFAULT_RESPONSE_TIMEOUT_S),
    )
    return session

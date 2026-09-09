"""Redis connection settings, in one place.

Every Redis client in this project used to be built as
``redis.from_url(url, decode_responses=True)``. redis-py applies no socket
timeout by default, and no call site wrapped its awaits in ``asyncio.wait_for``,
so a Redis that was *slow* rather than down never returned.

That distinction is the whole point. When Redis refuses the connection the
existing handlers work as designed — the rate limiter logs a warning and falls
back to per-instance RAM buckets. When Redis accepts the connection and then
stalls (memory pressure, a blocking command, a half-open socket after a network
partition), nothing raises, so nothing degrades: the await simply does not
return. Three request-path operations go through these clients — the rate
limiter's bucket acquire in the outermost middleware, one ``evalsha`` per
endpoint in the routing ring's circuit check, and the endpoint-stats update
after every completed request — and because the proxy runs one event loop, a
single hung await stalls every other in-flight request behind it.

With a timeout, redis-py raises ``TimeoutError``, which the ``except Exception``
fallbacks already catch. The degradation path written for a dead Redis starts
working for a sick one.

The defaults are deliberately short: every operation these clients perform is a
single Lua invocation or one HGETALL, so a second is already generous. Tune with
``caching.redis_socket_timeout`` / ``caching.redis_connect_timeout`` in
config.yaml, or with ``LLM_PROXY_REDIS_TIMEOUT`` where no config is in reach.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

logger = logging.getLogger("llmproxy.redis_client")

#: Seconds to wait for a reply before giving up on a Redis operation.
DEFAULT_SOCKET_TIMEOUT_S = 2.0
#: Seconds to wait for the TCP connection itself.
DEFAULT_CONNECT_TIMEOUT_S = 2.0


def _positive_float(value: Any, fallback: float) -> float:
    """Coerce a configured timeout, ignoring values that would disable it.

    A zero or negative timeout means "wait forever" to redis-py, which is the
    behaviour this module exists to remove — so it is treated as unset rather
    than honoured. A non-numeric value is a config error, not a request to hang.
    """
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return fallback
    return parsed if parsed > 0 else fallback


def resolve_timeouts(config: Optional[Dict[str, Any]] = None) -> Dict[str, float]:
    """Return the socket/connect timeouts for a Redis client.

    Precedence: caching.* in config, then LLM_PROXY_REDIS_TIMEOUT (which sets
    both), then the module defaults.
    """
    env_default = _positive_float(
        os.environ.get("LLM_PROXY_REDIS_TIMEOUT"), DEFAULT_SOCKET_TIMEOUT_S
    )
    cache_cfg = (config or {}).get("caching", {}) or {}
    return {
        "socket_timeout": _positive_float(
            cache_cfg.get("redis_socket_timeout"), env_default
        ),
        "socket_connect_timeout": _positive_float(
            cache_cfg.get("redis_connect_timeout"),
            _positive_float(
                os.environ.get("LLM_PROXY_REDIS_TIMEOUT"), DEFAULT_CONNECT_TIMEOUT_S
            ),
        ),
    }


def connect(
    redis_module: Any,
    redis_url: str,
    config: Optional[Dict[str, Any]] = None,
    **kwargs: Any,
) -> Any:
    """Build a Redis client that cannot wait forever.

    `redis_module` is passed in rather than imported here because each caller
    already guards its own optional ``import redis.asyncio`` and holds the
    module (or None) from that guard.
    """
    timeouts = resolve_timeouts(config)
    kwargs.setdefault("decode_responses", True)
    logger.debug(
        "Redis client: socket_timeout=%.1fs connect_timeout=%.1fs",
        timeouts["socket_timeout"],
        timeouts["socket_connect_timeout"],
    )
    return redis_module.from_url(redis_url, **timeouts, **kwargs)

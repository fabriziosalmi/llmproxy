"""
Rate Limiter — per-IP and per-key token bucket middleware.

Lightweight ASGI middleware with no external dependencies.
Configurable via config.yaml `rate_limiting` section.
"""

import time
import asyncio
import logging
from collections import OrderedDict
from typing import Dict, Optional, Tuple
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)


class TokenBucket:
    """Thread-safe async token bucket."""

    __slots__ = ("capacity", "rate", "_tokens", "_last_refill", "_lock")

    def __init__(self, capacity: int, rate: float):
        self.capacity = capacity
        self.rate = rate  # tokens per second
        self._tokens = float(capacity)
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> bool:
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_refill
            self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)
            self._last_refill = now
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return True
            return False

    @property
    def retry_after(self) -> float:
        """Seconds until next token is available."""
        if self._tokens >= 1.0:
            return 0.0
        return (1.0 - self._tokens) / self.rate


class RateLimiter:
    """Per-key rate limiter with automatic bucket creation and O(1) LRU eviction.

    Uses OrderedDict so that move_to_end() on every access keeps the mapping
    ordered from least-recently-used (front) to most-recently-used (back).
    Eviction is therefore O(1) — popitem(last=False) — instead of the O(N)
    min() scan that would hold the asyncio.Lock for the entire dictionary walk.
    """

    _MAX_BUCKETS = 50_000  # Prevent memory exhaustion from IP spray

    def __init__(self, default_capacity: int = 60, default_rate: float = 1.0):
        self.default_capacity = default_capacity
        self.default_rate = default_rate
        self._buckets: OrderedDict[str, TokenBucket] = OrderedDict()
        self._lock = asyncio.Lock()

    async def check(self, key: str) -> Tuple[bool, float]:
        """Returns (allowed, retry_after_seconds).

        H4: bucket.acquire() is now called inside the global lock to prevent
        a race where the bucket is evicted between lookup and acquire. The
        per-bucket lock is still held inside acquire() for token atomicity.
        """
        async with self._lock:
            if key not in self._buckets:
                if len(self._buckets) >= self._MAX_BUCKETS:
                    self._buckets.popitem(last=False)
                self._buckets[key] = TokenBucket(self.default_capacity, self.default_rate)
            else:
                self._buckets.move_to_end(key)

            bucket = self._buckets[key]
            allowed = await bucket.acquire()
            return allowed, bucket.retry_after


class RateLimitMiddleware(BaseHTTPMiddleware):
    """ASGI middleware that enforces per-IP rate limiting.

    Config (config.yaml):
        rate_limiting:
          enabled: true
          requests_per_minute: 60
          burst: 10              # Extra burst capacity above sustained rate
          exempt_paths:
            - /health
            - /metrics
    """

    def __init__(self, app, config: Optional[Dict] = None):
        super().__init__(app)
        cfg = (config or {}).get("rate_limiting", {})
        self.enabled = cfg.get("enabled", False)
        rpm = cfg.get("requests_per_minute", 60)
        burst = cfg.get("burst", 10)
        capacity = rpm + burst
        rate = rpm / 60.0  # tokens per second
        self.limiter = RateLimiter(default_capacity=capacity, default_rate=rate)
        self.exempt_paths = set(cfg.get("exempt_paths", ["/health", "/metrics"]))
        if self.enabled:
            logger.info(f"Rate limiter active: {rpm} req/min, burst={burst}")

    async def dispatch(self, request: Request, call_next):
        if not self.enabled:
            return await call_next(request)

        if request.url.path in self.exempt_paths:
            return await call_next(request)

        # Key: prefer API key prefix, fall back to IP
        auth = request.headers.get("authorization", "")
        if auth.startswith("Bearer ") and len(auth) > 15:
            key = f"key:{auth[7:15]}"  # First 8 chars of token
        else:
            key = f"ip:{request.client.host}" if request.client else "ip:unknown"

        allowed, retry_after = await self.limiter.check(key)
        if not allowed:
            logger.warning(f"Rate limit exceeded for {key}")
            return JSONResponse(
                status_code=429,
                content={"error": "Rate limit exceeded", "retry_after": round(retry_after, 1)},
                headers={"Retry-After": str(int(retry_after) + 1)},
            )

        return await call_next(request)

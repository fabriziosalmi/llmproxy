"""
Rate Limiter — per-IP and per-key token bucket middleware.

Lightweight ASGI middleware with no external dependencies.
Configurable via config.yaml `rate_limiting` section.
"""

import time
import asyncio
import logging
import hashlib
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

    async def acquire(self) -> Tuple[bool, float]:
        """Atomic check-and-take. Returns (allowed, retry_after_seconds).

        retry_after is computed under the same lock as the token mutation, so
        the value reflects the post-acquire state — no read-modify-write race
        with a concurrent acquire(). 0.0 when allowed; otherwise seconds
        until the next whole token refills.
        """
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_refill
            self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)
            self._last_refill = now
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return True, 0.0
            # Denied — compute retry_after under the same lock.
            retry_after = (1.0 - self._tokens) / self.rate if self.rate > 0 else float("inf")
            return False, retry_after

    @property
    def retry_after(self) -> float:
        """Advisory snapshot of seconds until the next token. Lock-free —
        intended only for read-only inspection (admin endpoints, tests).
        For correct retry hints in the request path, use the second value
        returned by `acquire()` which is computed atomically with the take.
        """
        if self._tokens >= 1.0:
            return 0.0
        if self.rate <= 0:
            return float("inf")
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

        R2-01: bucket.acquire() OUTSIDE the global lock to avoid serializing
        all rate checks behind one lock (starvation under high concurrency).
        The bucket reference held by the caller is safe even if evicted —
        it just becomes orphaned (extra token for one request, acceptable).
        """
        async with self._lock:
            if key not in self._buckets:
                if len(self._buckets) >= self._MAX_BUCKETS:
                    self._buckets.popitem(last=False)
                self._buckets[key] = TokenBucket(self.default_capacity, self.default_rate)
            else:
                self._buckets.move_to_end(key)
            bucket = self._buckets[key]

        return await bucket.acquire()


# N.6 — Named presets for runtime rate-limit tuning. Operators set one with
# POST /api/v1/rate-limit/preset; the middleware swaps default_capacity +
# default_rate and flushes existing buckets so the new caps take effect on
# the very next request. config.yaml still wins at boot — preset is applied
# on top after the orchestrator hydrates `rate_limit:preset` from the store.
RATE_LIMIT_PRESETS: Dict[str, Dict[str, int]] = {
    "strict":  {"requests_per_minute": 30,  "burst": 5},
    "normal":  {"requests_per_minute": 60,  "burst": 10},
    "relaxed": {"requests_per_minute": 240, "burst": 60},
}


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

    # Singleton reference: Starlette instantiates this once per app, and the
    # admin route uses the reference to mutate limits at runtime. Less ugly
    # than carrying the instance around through agent.app.middleware_stack.
    instance: "Optional[RateLimitMiddleware]" = None

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
        # Active preset name — None means "raw config values, no preset applied".
        self.preset: Optional[str] = None
        RateLimitMiddleware.instance = self
        if self.enabled:
            logger.info(f"Rate limiter active: {rpm} req/min, burst={burst}")

    async def apply_preset(self, name: str) -> Dict[str, int]:
        """Swap the active rpm/burst to the named preset and flush buckets.

        Existing buckets are dropped so the new caps apply immediately. New
        buckets are created on the next request with the new defaults.
        Returns the values applied so callers can echo them.
        """
        if name not in RATE_LIMIT_PRESETS:
            raise ValueError(f"Unknown preset '{name}'. Valid: {list(RATE_LIMIT_PRESETS)}")
        values = RATE_LIMIT_PRESETS[name]
        rpm = values["requests_per_minute"]
        burst = values["burst"]
        self.limiter.default_capacity = rpm + burst
        self.limiter.default_rate = rpm / 60.0
        async with self.limiter._lock:
            self.limiter._buckets.clear()
        self.preset = name
        logger.info(f"Rate limit preset applied: {name} ({rpm}/min, burst={burst})")
        return {"requests_per_minute": rpm, "burst": burst}

    def current_config(self) -> Dict[str, object]:
        """Expose the live tuning numbers to /api/v1/rate-limit/config."""
        cap = self.limiter.default_capacity
        rate = self.limiter.default_rate
        rpm = round(rate * 60)
        burst = max(0, cap - rpm)
        return {
            "enabled": self.enabled,
            "preset": self.preset,
            "requests_per_minute": rpm,
            "burst": burst,
            "default_capacity": cap,
            "default_rate_per_second": rate,
        }

    async def dispatch(self, request: Request, call_next):
        if not self.enabled:
            return await call_next(request)

        if request.url.path in self.exempt_paths:
            return await call_next(request)

        # Key: prefer API key prefix, fall back to IP
        auth = request.headers.get("authorization", "")
        token = ""
        if auth.lower().startswith("bearer "):
            token = auth[7:].strip()
        if token:
            # Use a stable hash of the full bearer token to avoid collisions
            # from short prefixes in multi-tenant environments.
            token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]
            key = f"key:{token_hash}"
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

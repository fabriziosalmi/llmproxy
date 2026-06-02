import pytest
from core.rate_limiter import TokenBucket, RateLimiter, RateLimitMiddleware
from core.threat_ledger import ThreatLedger
from starlette.requests import Request
from starlette.responses import JSONResponse


class MockSecurity:
    def __init__(self, threat_ledger):
        self.threat_ledger = threat_ledger


class MockAgent:
    def __init__(self, config, threat_ledger):
        self.config = config
        self.security = MockSecurity(threat_ledger)


@pytest.mark.asyncio
async def test_token_bucket_dynamic_throttling():
    # Base bucket: capacity 10, rate 2.0 (refill 2 tokens/sec)
    bucket = TokenBucket(capacity=10, rate=2.0)

    # Under throttle_factor = 0.5
    # capacity should scale to 5, rate should scale to 1.0
    allowed, retry_after = await bucket.acquire(
        throttle_factor=0.5, default_capacity=10.0, default_rate=2.0
    )
    assert allowed is True
    assert bucket.capacity == 5.0
    assert bucket.rate == 1.0

    # tokens should be capped to 5.0 (after acquire, it should be 4.0)
    assert bucket._tokens == 4.0

    # Under throttle_factor = 1.0 (restore)
    allowed, retry_after = await bucket.acquire(
        throttle_factor=1.0, default_capacity=10.0, default_rate=2.0
    )
    assert allowed is True
    assert bucket.capacity == 10.0
    assert bucket.rate == 2.0


@pytest.mark.asyncio
async def test_rate_limiter_dynamic_throttling():
    limiter = RateLimiter(default_capacity=10, default_rate=2.0)

    # First check with throttle_factor=0.5
    allowed, _ = await limiter.check("user1", throttle_factor=0.5)
    assert allowed is True
    bucket = limiter._buckets["user1"]
    assert bucket.capacity == 5.0
    assert bucket.rate == 1.0

    # Next check with throttle_factor=1.0
    allowed, _ = await limiter.check("user1", throttle_factor=1.0)
    assert allowed is True
    assert bucket.capacity == 10.0
    assert bucket.rate == 2.0


@pytest.mark.asyncio
async def test_rate_limit_middleware_throttling():
    config = {
        "rate_limiting": {
            "enabled": True,
            "requests_per_minute": 60,
            "burst": 0,
        }
    }

    # 60 rpm -> capacity = 60, rate = 1.0 token/sec
    # Create threat ledger with threshold 10.0
    threat_ledger = ThreatLedger(threshold=10.0, min_events=3)
    agent = MockAgent(config, threat_ledger)

    # Create middleware
    async def dummy_call_next(request):
        return JSONResponse({"status": "ok"})

    middleware = RateLimitMiddleware(app=None, config=config, agent=agent)

    # Initial state: no threat
    # Build a mock Starlette request
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/v1/chat",
        "headers": [(b"authorization", b"Bearer sk-mytoken123")],
        "client": ("127.0.0.1", 12345),
    }
    req = Request(scope)

    # Dispatch should allow (threat is 0)
    res = await middleware.dispatch(req, dummy_call_next)
    assert isinstance(res, JSONResponse)

    # Set high threat for the client
    # Let's record threat events in the ledger
    # Note: min_events is 3, threshold is 10.0
    # Let's record 3 events with score 2.0. Total = 6.0
    threat_ledger.record(ip="127.0.0.1", key_prefix="sk-mytok", score=2.0)
    threat_ledger.record(ip="127.0.0.1", key_prefix="sk-mytok", score=2.0)
    threat_ledger.record(ip="127.0.0.1", key_prefix="sk-mytok", score=2.0)

    # Max threat score is 6.0. Throttle factor = 1.0 - (6.0 / 10.0) = 0.4
    # With throttle factor 0.4, capacity becomes 60 * 0.4 = 24
    # Let's dispatch again and verify the bucket has been scaled
    res = await middleware.dispatch(req, dummy_call_next)
    assert isinstance(res, JSONResponse)

    # Wait, the token used is "sk-mytoken123", hash prefix is:
    import hashlib

    h = hashlib.sha256(b"sk-mytoken123").hexdigest()[:16]
    key = f"key:{h}"

    assert key in middleware.limiter._buckets
    bucket = middleware.limiter._buckets[key]
    assert bucket.capacity == pytest.approx(24.0)
    assert bucket.rate == pytest.approx(0.4)

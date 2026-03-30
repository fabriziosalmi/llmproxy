"""
Rate Limiter Tests — token bucket, per-key isolation, eviction, middleware.
"""

import asyncio
import pytest
from core.rate_limiter import TokenBucket, RateLimiter


# ── TokenBucket ──

@pytest.mark.asyncio
async def test_bucket_allows_within_capacity():
    bucket = TokenBucket(capacity=5, rate=1.0)
    for _ in range(5):
        assert await bucket.acquire() is True


@pytest.mark.asyncio
async def test_bucket_rejects_over_capacity():
    bucket = TokenBucket(capacity=2, rate=0.1)
    assert await bucket.acquire() is True
    assert await bucket.acquire() is True
    assert await bucket.acquire() is False


@pytest.mark.asyncio
async def test_bucket_refills_over_time():
    bucket = TokenBucket(capacity=1, rate=100.0)  # 100 tokens/sec
    assert await bucket.acquire() is True
    assert await bucket.acquire() is False
    await asyncio.sleep(0.02)  # 20ms → ~2 tokens refilled
    assert await bucket.acquire() is True


@pytest.mark.asyncio
async def test_bucket_retry_after():
    bucket = TokenBucket(capacity=1, rate=1.0)
    await bucket.acquire()
    await bucket.acquire()  # Drain
    assert bucket.retry_after > 0


# ── RateLimiter ──

@pytest.mark.asyncio
async def test_limiter_creates_buckets():
    limiter = RateLimiter(default_capacity=3, default_rate=1.0)
    allowed, _ = await limiter.check("user:alice")
    assert allowed is True
    assert "user:alice" in limiter._buckets


@pytest.mark.asyncio
async def test_limiter_isolates_keys():
    limiter = RateLimiter(default_capacity=1, default_rate=0.01)
    allowed_a, _ = await limiter.check("a")
    assert allowed_a is True
    # "a" is now drained, but "b" should still have tokens
    allowed_b, _ = await limiter.check("b")
    assert allowed_b is True
    # "a" should be rejected
    allowed_a2, _ = await limiter.check("a")
    assert allowed_a2 is False


@pytest.mark.asyncio
async def test_limiter_evicts_oldest():
    limiter = RateLimiter(default_capacity=5, default_rate=1.0)
    limiter._MAX_BUCKETS = 3
    for i in range(4):
        await limiter.check(f"key:{i}")
    assert len(limiter._buckets) <= 3

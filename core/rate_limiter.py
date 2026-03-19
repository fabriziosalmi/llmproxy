import time
import asyncio
from typing import Dict

class LeakyBucket:
    def __init__(self, capacity: int, leak_rate: float):
        """
        capacity: Maximum tokens the bucket can hold.
        leak_rate: Operations per second allowed.
        """
        self.capacity = capacity
        self.leak_rate = leak_rate
        self.tokens = 0.0
        self.last_leak_time = time.time()
        self._lock = asyncio.Lock()

    async def acquire(self, tokens: int = 1) -> bool:
        async with self._lock:
            self._leak()
            if self.tokens + tokens <= self.capacity:
                self.tokens += tokens
                return True
            return False

    def _leak(self):
        now = time.time()
        elapsed = now - self.last_leak_time
        leaked = elapsed * self.leak_rate
        self.tokens = max(0.0, self.tokens - leaked)
        self.last_leak_time = now

class DynamicRateLimiter:
    def __init__(self):
        self.buckets: Dict[str, LeakyBucket] = {}

    def get_bucket(self, key: str, capacity: int = 100, rate: float = 1.0) -> LeakyBucket:
        if key not in self.buckets:
            self.buckets[key] = LeakyBucket(capacity, rate)
        return self.buckets[key]

    def adjust_rate(self, key: str, new_rate: float):
        if key in self.buckets:
            self.buckets[key].leak_rate = new_rate

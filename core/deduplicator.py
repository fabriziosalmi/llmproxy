"""
Request deduplication — idempotency key support.

Prevents duplicate upstream calls when agentic clients retry aggressively.
If a request with the same X-Idempotency-Key arrives while the first is
still in-flight, the second waits for the first to complete and returns
the same response. Completed responses are cached for TTL seconds.
"""

import time
import asyncio
import logging
from typing import Dict

logger = logging.getLogger("llmproxy.deduplicator")


class RequestDeduplicator:
    """Deduplicates in-flight and recently completed requests."""

    def __init__(self, ttl_seconds: int = 300):
        self._in_flight: Dict[str, asyncio.Future] = {}
        self._completed: Dict[str, tuple] = {}  # key → (response, expires_at)
        self._ttl = ttl_seconds
        self._lock = asyncio.Lock()

    async def execute_or_wait(self, key: str, coro):
        """Execute coroutine or wait for identical in-flight request.

        Args:
            key: Unique idempotency key (e.g. "{session}:{X-Idempotency-Key}")
            coro: The coroutine to execute if no duplicate is found

        Returns:
            The response (from execution or from the duplicate)
        """
        is_executor = False
        async with self._lock:
            # 1. Already completed? Return cached response.
            if key in self._completed:
                response, expires = self._completed[key]
                if time.time() < expires:
                    logger.debug(f"Dedup HIT (cached): {key[:16]}...")
                    return response
                else:
                    del self._completed[key]

            # 2. In-flight? Grab the existing future to await after releasing lock.
            if key in self._in_flight:
                future = self._in_flight[key]
            else:
                # 3. First time: register in-flight future; we are the executor.
                future = asyncio.get_running_loop().create_future()
                self._in_flight[key] = future
                is_executor = True

        if not is_executor:
            # Wait for the executor to finish (lock already released).
            logger.debug(f"Dedup WAIT (in-flight): {key[:16]}...")
            return await future

        # We are the executor — run the coroutine outside the lock.
        try:
            result = await coro
            async with self._lock:
                self._completed[key] = (result, time.time() + self._ttl)
            if not future.done():
                future.set_result(result)
            return result
        except Exception as e:
            if not future.done():
                future.set_exception(e)
            raise
        finally:
            async with self._lock:
                self._in_flight.pop(key, None)

    def cleanup_expired(self):
        """Remove expired entries from the completed cache."""
        now = time.time()
        expired = [k for k, (_, exp) in self._completed.items() if now >= exp]
        for k in expired:
            del self._completed[k]

    @property
    def stats(self) -> Dict[str, int]:
        return {
            "in_flight": len(self._in_flight),
            "cached": len(self._completed),
        }

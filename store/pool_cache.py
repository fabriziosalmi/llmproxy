"""A short-lived snapshot of the verified endpoint pool.

select_endpoint runs for every proxied request and asked the store for the pool
each time: a SELECT, a json.loads per row and a pydantic LLMEndpoint (with its
HttpUrl) per row, then a Python filter. With SQLite that is a local read and
with Postgres a network query, on the path every request takes, and the pool
changes only when an operator adds, removes or re-statuses an endpoint.

The stores keep the snapshot and drop it inside every write that can change it.
The TTL is only a backstop for a writer the store does not see (another process
on the same database), so a missed invalidation costs seconds of staleness, not
correctness. A read that was in flight when a write invalidated the snapshot is
not allowed to store its (older) result.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

#: Backstop only; writes through the store invalidate immediately.
DEFAULT_TTL_S = 5.0


class PoolCache:
    def __init__(
        self, ttl_s: float = DEFAULT_TTL_S, clock: Callable[[], float] = time.monotonic
    ):
        self._ttl_s = ttl_s
        self._clock = clock
        self._pool: list[Any] | None = None
        self._stored_at = 0.0
        self._generation = 0

    @property
    def generation(self) -> int:
        """Capture before reading from the database; pass back to put()."""
        return self._generation

    def get(self) -> list[Any] | None:
        """A copy of the snapshot, or None when absent or expired.

        The list is copied; the endpoint objects are shared and must be treated
        as read-only by callers (the router only reads them).
        """
        if self._pool is None or self._clock() - self._stored_at >= self._ttl_s:
            return None
        return list(self._pool)

    def put(self, pool: list[Any], generation: int) -> None:
        if generation != self._generation:
            return  # a write landed while we were reading; this is already stale
        self._pool = list(pool)
        self._stored_at = self._clock()

    def invalidate(self) -> None:
        self._generation += 1
        self._pool = None

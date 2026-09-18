"""Refuse excess work instead of accumulating it.

The only ceiling on concurrent upstream work was the aiohttp connector's
`limit` (connection_pool.max_connections, 100 by default). Past it, requests
did not fail — they waited in the connector's internal queue, which is
unbounded, and the wait had no deadline: `total` is deliberately None (a shared
total truncates long completions and makes the forwarder retry them against a
second provider), and sock_connect and sock_read only start counting once a
connection has been acquired.

So overload converted into latency that grew without limit and memory that grew
with it — each parked request holding its parsed body, its PluginContext, its
per-request PII vault and its session state — rather than into a refusal the
caller could back off from. Nothing above provided admission control: there is
no in-flight cap, and the rate limiter is a per-IP and per-key token bucket,
which shapes one caller and says nothing about aggregate concurrency, so a
hundred well-behaved keys saturate the proxy without any of them being
throttled.

This adds the missing ceiling: a semaphore sized from the connection pool, plus
a bounded waiting room. Beyond both, the answer is 503 with Retry-After, which
is a thing a client can act on.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger("llmproxy.admission")

#: How many requests may wait for a slot, as a multiple of the slot count.
#
# Some queueing is right — it absorbs bursts that would otherwise be refused
# for no reason — but it has to end somewhere. Two means a proxy sized for 100
# concurrent upstream calls will hold at most 200 more before it starts saying
# no, which at that point is the honest answer.
DEFAULT_QUEUE_FACTOR = 2.0

#: Seconds to advertise in Retry-After when the waiting room is full.
DEFAULT_RETRY_AFTER_S = 1


class AdmissionController:
    """Bounds how many data-plane requests are in flight at once."""

    def __init__(
        self,
        max_in_flight: int,
        max_queued: int,
        retry_after_s: int = DEFAULT_RETRY_AFTER_S,
    ):
        self.max_in_flight = max_in_flight
        self.max_queued = max_queued
        self.retry_after_s = retry_after_s
        self._semaphore = asyncio.Semaphore(max_in_flight) if max_in_flight > 0 else None
        self._queued = 0
        self._queued_lock = asyncio.Lock()

    @classmethod
    def from_config(cls, config: Optional[Dict[str, Any]]) -> "AdmissionController":
        """Size from connection_pool, because that is the real constraint.

        Admitting more requests than the connector can serve just moves the
        queue from here into aiohttp, which is where it was unbounded.
        """
        cfg = config or {}
        pool_cfg = cfg.get("connection_pool", {}) or {}
        admission_cfg = cfg.get("admission", {}) or {}

        max_in_flight = admission_cfg.get(
            "max_in_flight", pool_cfg.get("max_connections", 100)
        )
        queue_factor = admission_cfg.get("queue_factor", DEFAULT_QUEUE_FACTOR)
        max_queued = admission_cfg.get(
            "max_queued", int(max_in_flight * queue_factor)
        )
        return cls(
            max_in_flight=max_in_flight,
            max_queued=max_queued,
            retry_after_s=admission_cfg.get("retry_after_s", DEFAULT_RETRY_AFTER_S),
        )

    @property
    def enabled(self) -> bool:
        return self._semaphore is not None

    @property
    def in_flight(self) -> int:
        if self._semaphore is None:
            return 0
        return self.max_in_flight - self._semaphore._value  # noqa: SLF001

    @property
    def queued(self) -> int:
        return self._queued

    async def acquire(self) -> bool:
        """Take a slot, waiting if necessary. False means "refuse this one".

        Refusal happens only when the waiting room is already full, so a burst
        is absorbed and a sustained overload is shed.
        """
        if self._semaphore is None:
            return True

        # A free slot is taken immediately. The queue bound applies only to
        # requests that would otherwise WAIT — checking it first meant
        # max_queued=0 refused every request, including ones a slot was
        # available for.
        if not self._semaphore.locked():
            await self._semaphore.acquire()
            return True

        async with self._queued_lock:
            if self._queued >= self.max_queued:
                return False
            self._queued += 1
        try:
            await self._semaphore.acquire()
        finally:
            async with self._queued_lock:
                self._queued -= 1
        return True

    def release(self) -> None:
        if self._semaphore is not None:
            self._semaphore.release()

"""Audit and spend writes that are queued behind the store, bounded.

The chat route persisted each request's spend and audit rows in a background task
of its own. Every such write waits for the same single lock (the audit chain is
linear), so when the store is slower than the request rate the tasks pile up with
no limit and nothing says so: memory grows with the backlog, rows reach the log
later and later, and a crash loses everything still waiting.

Below ``max_pending`` writes stay off the request path, as before. At the limit the
request awaits its own write: the backlog stops growing and the caller feels the
store's speed, which is the honest signal. ``llm_proxy_audit_backlog`` shows how
deep it is, and ``audit_persistence_total{outcome="backpressure"}`` counts the
requests that had to wait.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any

from core.metrics import MetricsTracker

logger = logging.getLogger("llmproxy.audit_backlog")

DEFAULT_MAX_PENDING = 1000

_pending = 0


def pending() -> int:
    return _pending


def reset_for_tests() -> None:
    global _pending
    _pending = 0
    MetricsTracker.set_audit_backlog(0)


async def submit(agent: Any, work: Coroutine[Any, Any, None], *, route: str) -> None:
    """Run ``work`` in the background, or inline when the backlog is at its limit."""
    global _pending
    limit = int(
        ((getattr(agent, "config", None) or {}).get("audit", {}) or {}).get(
            "max_pending_writes", DEFAULT_MAX_PENDING
        )
    )

    async def counted() -> None:
        global _pending
        try:
            await work
        finally:
            _pending -= 1
            MetricsTracker.set_audit_backlog(_pending)

    _pending += 1
    MetricsTracker.set_audit_backlog(_pending)

    if limit > 0 and _pending > limit:
        MetricsTracker.track_audit_persistence(route, "backpressure")
        await counted()
        return

    task = agent._spawn_task(counted())

    def _log_failure(t: asyncio.Task) -> None:
        if not t.cancelled() and t.exception() is not None:
            logger.warning("Audit log persistence failed: %s", t.exception())

    task.add_done_callback(_log_failure)

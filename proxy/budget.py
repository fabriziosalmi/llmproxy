"""LLMProxy — Budget charging + persistence helper.

Single source of truth for "increment today's spend AND persist it". Used
by every cost-charging path:

  - request_pipeline.process_proxy_request (non-streaming charges)
  - forwarder._handle_streaming finally block (streaming charges)
  - routes/embeddings.create_router (embeddings charges)
  - routes/chat.create_router (post-call persistence sweep)

Without the persistence step, a charge lives only in `rotator.total_cost_today`
(in-memory) until either (a) a chat request happens to enqueue it via
`agent.enqueue_write`, or (b) a graceful shutdown drains the queue. A
crash between charge and persist loses the spend.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger("llmproxy.budget")


async def charge_and_persist(
    rotator: Any, lock: asyncio.Lock, amount: float,
) -> None:
    """Atomically add `amount` to `rotator.total_cost_today` and enqueue
    a persistence write.

    `lock` MUST NOT already be held by the caller — the helper acquires
    it. The caller passes both the lock and the rotator so the same
    lock instance protects every accountant in the codebase.

    Persistence failures are swallowed (logged at debug). The increment
    has already happened — losing the disk write means the next chat
    request's enqueue will eventually pick up the new total, and the
    audit/spend ledger is the authoritative record regardless.
    """
    if amount == 0:
        return
    async with lock:
        rotator.total_cost_today += amount
        try:
            rotator.enqueue_write(
                "budget:daily_total", rotator.total_cost_today,
            )
        except Exception as e:  # noqa: BLE001 — never let persistence kill the request
            logger.debug(f"Budget enqueue skipped: {e}")

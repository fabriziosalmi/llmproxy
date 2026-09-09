"""LLMProxy — Budget charging + persistence helpers.

Two responsibilities:

  1. `charge_and_persist(rotator, lock, amount)` — increment today's spend
     atomically and enqueue persistence. Called by every cost-charging
     site (forwarder streaming finally, embeddings cost block, …).

  2. `hydrate_daily_total(store)` — read today's budget state on startup,
     reset to 0 if the saved date is stale, return (total, today). Owns
     the daily-rollover policy.

Without (1), a charge lives only in `rotator.total_cost_today` until a
chat request happens to enqueue it; a crash between charge and persist
loses the spend.

Without (2), the orchestrator's setup() inlined the same logic — split
out so tests can exercise the hydration policy without spinning up a
full orchestrator.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import logging
from typing import Any, Tuple

logger = logging.getLogger("llmproxy.budget")


async def charge_and_persist(
    rotator: Any,
    lock: asyncio.Lock,
    amount: float,
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
                "budget:daily_total",
                rotator.total_cost_today,
            )
        except Exception as e:  # noqa: BLE001 — never let persistence kill the request
            logger.debug(f"Budget enqueue skipped: {e}")

        # Publish the gauge here, where every charging site already funnels.
        #
        # It used to be set in the chat route alone, so a deployment whose
        # clients use /v1/completions, /v1/embeddings or streaming spent real
        # money while llm_proxy_budget_consumed_usd sat at whatever the last
        # non-streaming chat request left it — and the shipped alert rules
        # (monitoring/prometheus-rules.yml warns at 40 and pages at 50) watch
        # exactly that series. The ledger recorded the spend; the signal an
        # operator would page on did not.
        try:
            from core.metrics import MetricsTracker

            daily_limit = (getattr(rotator, "config", None) or {}).get(
                "budget", {}
            ).get("daily_limit", 0.0)
            MetricsTracker.set_budget(rotator.total_cost_today, daily_limit)
        except Exception as e:  # noqa: BLE001 — a gauge must never fail a request
            logger.debug(f"Budget gauge update skipped: {e}")


async def hydrate_daily_total(store: Any) -> Tuple[float, str]:
    """Restore today's budget on startup, applying the daily-rollover
    policy.

    Reads `budget:daily_date` from `store`:
      - if it matches today's ISO date → return saved `budget:daily_total`
      - otherwise → reset to 0.0, persist today's date + 0.0 total

    Returns `(total, today_iso)`. The orchestrator stores both:
    `total` → `self.total_cost_today`, `today_iso` → `self._budget_date`.
    """
    today = _dt.date.today().isoformat()
    saved_date = await store.get_state("budget:daily_date", None)
    if saved_date == today:
        total = await store.get_state("budget:daily_total", 0.0)
        return float(total), today
    # Date changed (or first boot) — reset.
    await store.set_state("budget:daily_date", today)
    await store.set_state("budget:daily_total", 0.0)
    return 0.0, today

"""The daily budget starts a new day at midnight, not at the next restart.

hydrate_daily_total applied the rollover once, at boot. A process that ran past
midnight kept adding to yesterday's total, so the "daily" limit was cumulative
since the last restart: once spend crossed it, every request was refused with a
402 until someone restarted the proxy. Restarting after midnight reset the total
in a way no running instance ever did.
"""

import asyncio
import datetime as dt
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import Request

from core.plugin_engine import PluginState
from proxy import budget
from proxy.budget import charge_and_persist, roll_over_if_new_day
from proxy.request_pipeline import process_proxy_request

YESTERDAY = "2026-10-07"
TODAY = "2026-10-08"


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    """Pin the local date the budget module sees."""

    class FakeDate(dt.date):
        @classmethod
        def today(cls):
            return cls.fromisoformat(state["today"])

    state = {"today": TODAY}
    monkeypatch.setattr(budget, "_dt", SimpleNamespace(date=FakeDate))
    return state


def _rotator(total, date):
    writes = []
    return SimpleNamespace(
        total_cost_today=total,
        _budget_date=date,
        config={"budget": {"daily_limit": 50.0}},
        enqueue_write=lambda key, value: writes.append((key, value)),
        writes=writes,
    )


def test_a_new_day_zeroes_the_total_and_persists_the_date():
    r = _rotator(49.0, YESTERDAY)

    assert roll_over_if_new_day(r) is True

    assert r.total_cost_today == 0.0
    assert r._budget_date == TODAY
    assert ("budget:daily_date", TODAY) in r.writes
    assert ("budget:daily_total", 0.0) in r.writes


def test_the_same_day_changes_nothing():
    r = _rotator(12.5, TODAY)

    assert roll_over_if_new_day(r) is False

    assert r.total_cost_today == 12.5
    assert r.writes == []


def test_an_orchestrator_that_never_hydrated_keeps_the_total_it_has():
    r = _rotator(7.0, None)

    assert roll_over_if_new_day(r) is False

    assert r.total_cost_today == 7.0
    assert r._budget_date == TODAY


async def test_a_charge_after_midnight_counts_only_toward_the_new_day():
    r = _rotator(49.0, YESTERDAY)

    await charge_and_persist(r, asyncio.Lock(), 0.5)

    assert r.total_cost_today == 0.5
    assert ("budget:daily_total", 0.5) in r.writes


async def test_the_limit_check_does_not_see_yesterdays_spend():
    """The check that returns 402: yesterday's 49.0 must not count today."""
    orchestrator = MagicMock()
    orchestrator.config = {"budget": {"daily_limit": 50.0}}
    orchestrator.total_cost_today = 49.0
    orchestrator._budget_date = YESTERDAY
    orchestrator._budget_lock = asyncio.Lock()
    orchestrator.plugin_manager = MagicMock()
    orchestrator.plugin_manager.execute_ring = AsyncMock()
    orchestrator.security = MagicMock()
    orchestrator.security.inspect = AsyncMock(return_value=None)
    orchestrator.negative_cache = MagicMock()
    orchestrator.negative_cache.check = MagicMock(return_value=None)
    orchestrator.forwarder = MagicMock()
    orchestrator.forwarder.forward_with_fallback = AsyncMock()
    orchestrator.plugin_state = PluginState(
        cache=None, metrics=MagicMock(), config={}, extra={}
    )

    request = MagicMock(spec=Request)
    request.json = AsyncMock(
        return_value={
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "A" * 4_000_000}],  # ~$2.50
        }
    )
    request.headers = {}
    request.state = MagicMock()
    request.state.quota_exceeded = False

    try:
        await process_proxy_request(orchestrator, request)
    except Exception:
        pass  # everything past the budget check is mocked; only its effect matters

    assert orchestrator.total_cost_today == 0.0
    assert orchestrator._budget_date == TODAY
    logged = " ".join(str(c) for c in orchestrator._add_log.call_args_list)
    assert "BUDGET SATURATED" not in logged


async def test_the_rollover_loop_resets_without_traffic(monkeypatch):
    from proxy import background

    r = _rotator(30.0, YESTERDAY)
    r._budget_lock = asyncio.Lock()
    ticks = {"n": 0}

    async def two_ticks(_):
        ticks["n"] += 1
        if ticks["n"] > 1:
            raise asyncio.CancelledError

    monkeypatch.setattr(background.asyncio, "sleep", two_ticks)
    with pytest.raises(asyncio.CancelledError):
        await background.budget_rollover_loop(r, 30)

    assert r.total_cost_today == 0.0
    assert r._budget_date == TODAY

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import Request

from core.plugin_engine import PluginState
from proxy.request_pipeline import process_proxy_request


def _orchestrator(spent: float):
    orchestrator = MagicMock()
    orchestrator.config = {"budget": {"daily_limit": 50.0}}
    orchestrator.total_cost_today = spent
    orchestrator._budget_date = None
    orchestrator._budget_lock = asyncio.Lock()
    orchestrator._add_log = AsyncMock()
    orchestrator._get_session = AsyncMock()
    orchestrator.enqueue_write = MagicMock()
    orchestrator._spawn_task = lambda coro: coro.close()  # no background ring here
    orchestrator.plugin_manager = MagicMock()
    orchestrator.plugin_manager.execute_ring = AsyncMock()
    orchestrator.security = MagicMock()
    orchestrator.security.inspect = AsyncMock(return_value=None)
    orchestrator.negative_cache = MagicMock()
    orchestrator.negative_cache.check = MagicMock(return_value=None)
    orchestrator.plugin_state = PluginState(
        cache=None, metrics=MagicMock(), config={}, extra={}
    )
    seen = {}

    async def forward(ctx, target, headers, session, cost_ref=None):
        # What the real forwarder reads: the pipeline's budget verdict.
        seen["saturated"] = ctx.metadata.get("_budget_saturated", False)
        from starlette.responses import Response

        ctx.response = Response(content=b"{}", status_code=200)
        return ctx.response

    orchestrator.forwarder = MagicMock()
    orchestrator.forwarder.forward_with_fallback = AsyncMock(side_effect=forward)
    return orchestrator, seen


def _request(content: str):
    request = MagicMock(spec=Request)
    request.json = AsyncMock(
        return_value={"model": "gpt-4o", "messages": [{"role": "user", "content": content}]}
    )
    request.headers = {}
    request.state = MagicMock()
    request.state.quota_exceeded = False
    return request


@pytest.mark.asyncio
async def test_a_request_that_would_cross_the_limit_is_marked_saturated():
    """49.0 spent + a ~2.50 USD request (1M tokens) > the 50.0 limit.

    This used to end in `assert True` after swallowing every exception, so it
    could not fail; the verdict the forwarder acts on (HTTP 402) was untested.
    """
    orchestrator, seen = _orchestrator(spent=49.0)

    await process_proxy_request(orchestrator, _request("A" * 4_000_000))

    assert seen["saturated"] is True
    logged = " ".join(str(c) for c in orchestrator._add_log.call_args_list)
    assert "BUDGET SATURATED (Global)" in logged


@pytest.mark.asyncio
async def test_a_request_well_inside_the_limit_is_not():
    orchestrator, seen = _orchestrator(spent=1.0)

    await process_proxy_request(orchestrator, _request("hello"))

    assert seen["saturated"] is False
    logged = " ".join(str(c) for c in orchestrator._add_log.call_args_list)
    assert "BUDGET SATURATED" not in logged


@pytest.mark.asyncio
async def test_exactly_at_the_limit_with_any_cost_is_saturated():
    orchestrator, seen = _orchestrator(spent=50.0)

    await process_proxy_request(orchestrator, _request("hello"))

    assert seen["saturated"] is True

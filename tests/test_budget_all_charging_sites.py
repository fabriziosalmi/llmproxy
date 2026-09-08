"""Every site that charges the budget must also persist it.

proxy/budget.py exists so that "increment today's spend AND enqueue the
persistence write, under one lock" happens in one place, and its docstring
says it is called by every cost-charging site. Two of the three called it:
the streaming finally block in forwarder.py and the embeddings route. The
non-streaming path in request_pipeline.py took the same lock and incremented
the counter inline, without enqueuing.

/v1/chat/completions hid that, because its route enqueues the total
separately after each request. /v1/completions has no such route-level
enqueue — it reaches the pipeline through agent.proxy_request — so a
non-streaming workload there advanced the in-memory total while the persisted
app_state row stayed where the last chat request left it. On restart
hydrate_daily_total reads that row, so the day's spend resets downward while
the daily limit keeps being enforced against it.
"""

import pathlib
import re

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent

# Every module that adds to the running spend total.
CHARGING_SITES = [
    "proxy/request_pipeline.py",
    "proxy/forwarder.py",
    "proxy/routes/embeddings.py",
]


@pytest.mark.parametrize("path", CHARGING_SITES)
def test_charging_site_routes_through_the_helper(path):
    source = (REPO / path).read_text()
    assert "charge_and_persist" in source, (
        f"{path} charges the budget without referencing charge_and_persist; "
        "an increment that skips it updates memory and not the store"
    )


@pytest.mark.parametrize("path", CHARGING_SITES)
def test_no_charging_site_increments_the_counter_inline(path):
    """The specific regression: += on the counter instead of the helper."""
    source = (REPO / path).read_text()
    inline = re.findall(r"^\s*\w+\.total_cost_today\s*\+=.*$", source, re.M)
    assert not inline, (
        f"{path} increments total_cost_today directly:\n  "
        + "\n  ".join(i.strip() for i in inline)
        + "\nUse charge_and_persist so the store is updated in the same step."
    )


@pytest.mark.asyncio
async def test_helper_increments_and_enqueues_together():
    """Both halves happen, or neither — that is the point of the helper."""
    import asyncio
    from unittest.mock import MagicMock

    from proxy.budget import charge_and_persist

    rotator = MagicMock()
    rotator.total_cost_today = 0.0
    enqueued = []
    rotator.enqueue_write = lambda k, v: enqueued.append((k, v))

    await charge_and_persist(rotator, asyncio.Lock(), 0.25)

    assert rotator.total_cost_today == 0.25
    assert enqueued == [("budget:daily_total", 0.25)], (
        "the persistence write must accompany the increment"
    )


@pytest.mark.asyncio
async def test_a_zero_charge_does_not_touch_the_store():
    import asyncio
    from unittest.mock import MagicMock

    from proxy.budget import charge_and_persist

    rotator = MagicMock()
    rotator.total_cost_today = 1.0
    enqueued = []
    rotator.enqueue_write = lambda k, v: enqueued.append((k, v))

    await charge_and_persist(rotator, asyncio.Lock(), 0.0)

    assert rotator.total_cost_today == 1.0
    assert enqueued == []

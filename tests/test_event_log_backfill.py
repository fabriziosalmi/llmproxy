"""SSE log backfill: the Live Logs page and the threat event feed subscribe to a
live-only stream, so EventLogger keeps a replayable ring of recent entries that
is sent on connect. Without it the pages render blank until the next event."""
import asyncio

import pytest

from proxy.event_log import EventLogger


@pytest.mark.asyncio
async def test_recent_logs_rings_to_capacity():
    el = EventLogger(log_history_size=5)
    for i in range(8):
        await el.add_log(f"msg{i}")
    msgs = [e["message"] for e in el.recent_logs()]
    # Oldest-first, capped at the ring size, keeping the newest entries.
    assert msgs == ["msg3", "msg4", "msg5", "msg6", "msg7"]


@pytest.mark.asyncio
async def test_recent_logs_empty_before_any_event():
    assert EventLogger().recent_logs() == []


@pytest.mark.asyncio
async def test_recent_logs_preserves_level_and_metadata():
    el = EventLogger(log_history_size=10)
    await el.add_log("blocked injection", level="SECURITY", metadata={"ip": "1.2.3.4"})
    [entry] = el.recent_logs()
    assert entry["level"] == "SECURITY"
    assert entry["metadata"] == {"ip": "1.2.3.4"}


@pytest.mark.asyncio
async def test_history_independent_of_live_subscribers():
    """History accumulates even with no SSE subscriber attached, so a client that
    connects later still gets the backfill."""
    el = EventLogger(log_history_size=10)
    await el.add_log("before anyone subscribed")
    q = el.subscribe_logs()
    await el.add_log("after subscribe")
    # Live subscriber only sees post-subscribe events...
    assert q.get_nowait()["message"] == "after subscribe"
    assert q.empty()
    # ...but the replay ring carries both for a fresh connect.
    assert [e["message"] for e in el.recent_logs()] == [
        "before anyone subscribed",
        "after subscribe",
    ]

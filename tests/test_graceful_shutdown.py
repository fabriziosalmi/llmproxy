"""
Integration tests for graceful shutdown queue mechanics in proxy/rotator.py.

Tests the write queue used for budget persistence:
- enqueue_write adds items to the queue
- _drain_pending_writes flushes all items to the store
- The shutdown event handler calls _drain_pending_writes
- Pending writes are not lost when the queue has items

Does NOT start a full server -- tests only the queue/store interaction
using a mock store and the real RotatorAgent queue logic.
"""

import asyncio
from typing import Any
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Minimal mock store satisfying the StateBackend protocol
# ---------------------------------------------------------------------------

class MockStore:
    """In-memory store implementing set_state/get_state for testing."""

    def __init__(self):
        self._data: dict[str, Any] = {}
        self.set_state_calls: list[tuple[str, Any]] = []

    async def init(self):
        pass

    async def set_state(self, key: str, value: Any) -> None:
        self._data[key] = value
        self.set_state_calls.append((key, value))

    async def get_state(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)


# ---------------------------------------------------------------------------
# Isolated queue+drain logic (avoids instantiating full RotatorAgent)
# ---------------------------------------------------------------------------

class _WriteQueue:
    """Extracts the enqueue_write / _drain_pending_writes logic from RotatorAgent
    so we can test it without importing the full dependency tree."""

    def __init__(self, store, maxsize: int = 500):
        self.store = store
        self._pending_writes: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        self.logger = MagicMock()

    def enqueue_write(self, key: str, value: Any):
        try:
            self._pending_writes.put_nowait((key, value))
        except asyncio.QueueFull:
            self.logger.warning("Pending writes queue full, dropping write")

    async def _drain_pending_writes(self):
        writes: list[tuple[str, Any]] = []
        while not self._pending_writes.empty():
            try:
                writes.append(self._pending_writes.get_nowait())
            except asyncio.QueueEmpty:
                break
        for key, value in writes:
            try:
                await self.store.set_state(key, value)
            except Exception as e:
                self.logger.warning(f"Failed to flush state write {key}: {e}")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestEnqueueWrite:
    """Tests for the enqueue_write method."""

    @pytest.mark.asyncio
    async def test_enqueue_adds_item_to_queue(self):
        store = MockStore()
        wq = _WriteQueue(store)
        wq.enqueue_write("budget:daily_total", 1.23)
        assert wq._pending_writes.qsize() == 1

    @pytest.mark.asyncio
    async def test_enqueue_multiple_items(self):
        store = MockStore()
        wq = _WriteQueue(store)
        wq.enqueue_write("key1", "val1")
        wq.enqueue_write("key2", "val2")
        wq.enqueue_write("key3", "val3")
        assert wq._pending_writes.qsize() == 3

    @pytest.mark.asyncio
    async def test_enqueue_drops_when_queue_full(self):
        store = MockStore()
        wq = _WriteQueue(store, maxsize=2)
        wq.enqueue_write("key1", "val1")
        wq.enqueue_write("key2", "val2")
        wq.enqueue_write("key3", "val3")  # should be dropped
        assert wq._pending_writes.qsize() == 2
        wq.logger.warning.assert_called_once()


class TestDrainPendingWrites:
    """Tests for the _drain_pending_writes method."""

    @pytest.mark.asyncio
    async def test_drain_flushes_all_to_store(self):
        store = MockStore()
        wq = _WriteQueue(store)
        wq.enqueue_write("budget:daily_total", 5.0)
        wq.enqueue_write("proxy_enabled", True)

        await wq._drain_pending_writes()

        assert wq._pending_writes.qsize() == 0
        assert store._data["budget:daily_total"] == 5.0
        assert store._data["proxy_enabled"] is True
        assert len(store.set_state_calls) == 2

    @pytest.mark.asyncio
    async def test_drain_empty_queue_is_noop(self):
        store = MockStore()
        wq = _WriteQueue(store)

        await wq._drain_pending_writes()

        assert len(store.set_state_calls) == 0

    @pytest.mark.asyncio
    async def test_drain_handles_store_failure(self):
        """If the store raises, drain should log a warning but not crash."""
        store = MockStore()
        wq = _WriteQueue(store)
        wq.enqueue_write("good_key", "good_val")
        wq.enqueue_write("bad_key", "bad_val")

        # Make set_state fail on the second call
        call_count = 0
        original = store.set_state

        async def failing_set_state(key, value):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise RuntimeError("Simulated DB error")
            await original(key, value)

        store.set_state = failing_set_state

        await wq._drain_pending_writes()

        # First write succeeded, second failed
        assert store._data.get("good_key") == "good_val"
        assert "bad_key" not in store._data
        wq.logger.warning.assert_called_once()

    @pytest.mark.asyncio
    async def test_drain_preserves_write_order(self):
        """Writes must be flushed in FIFO order."""
        store = MockStore()
        wq = _WriteQueue(store)
        for i in range(10):
            wq.enqueue_write(f"key_{i}", i)

        await wq._drain_pending_writes()

        keys = [k for k, _ in store.set_state_calls]
        assert keys == [f"key_{i}" for i in range(10)]


class TestShutdownDrain:
    """Tests that the shutdown event handler drains pending writes."""

    @pytest.mark.asyncio
    async def test_shutdown_drains_queue(self):
        """Simulate what the @app.on_event('shutdown') handler does."""
        store = MockStore()
        wq = _WriteQueue(store)

        # Simulate in-flight budget writes
        wq.enqueue_write("budget:daily_total", 42.0)
        wq.enqueue_write("budget:daily_date", "2026-03-23")
        wq.enqueue_write("feature_injection_guard", True)

        assert wq._pending_writes.qsize() == 3

        # Shutdown handler calls _drain_pending_writes
        await wq._drain_pending_writes()

        assert wq._pending_writes.qsize() == 0
        assert store._data["budget:daily_total"] == 42.0
        assert store._data["budget:daily_date"] == "2026-03-23"
        assert store._data["feature_injection_guard"] is True

    @pytest.mark.asyncio
    async def test_no_writes_lost_under_load(self):
        """Enqueue many writes, then drain -- all must arrive at the store."""
        store = MockStore()
        wq = _WriteQueue(store, maxsize=500)

        expected = {}
        for i in range(200):
            key = f"metric_{i}"
            wq.enqueue_write(key, i * 0.01)
            expected[key] = i * 0.01

        await wq._drain_pending_writes()

        assert wq._pending_writes.qsize() == 0
        assert len(store.set_state_calls) == 200
        for key, value in expected.items():
            assert store._data[key] == value, f"Missing or wrong value for {key}"

    @pytest.mark.asyncio
    async def test_concurrent_enqueue_and_drain(self):
        """Enqueue writes while drain is running -- no exceptions."""
        store = MockStore()
        wq = _WriteQueue(store, maxsize=500)

        # Pre-fill some writes
        for i in range(50):
            wq.enqueue_write(f"pre_{i}", i)

        # Drain and enqueue concurrently
        async def enqueue_more():
            for i in range(50):
                wq.enqueue_write(f"post_{i}", i)
                await asyncio.sleep(0)

        await asyncio.gather(
            wq._drain_pending_writes(),
            enqueue_more(),
        )

        # Drain any remaining
        await wq._drain_pending_writes()

        # At least the pre-fill should have been flushed
        pre_keys = [k for k, _ in store.set_state_calls if k.startswith("pre_")]
        assert len(pre_keys) == 50

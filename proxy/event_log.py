"""
LLMPROXY — Event Logger.

Bounded async queues for log entries and telemetry events,
with dead-letter queue (DLQ) fallback for back-pressure.
"""

import json
import time
import asyncio
import logging
from datetime import datetime
from typing import Dict, Any

logger = logging.getLogger("llmproxy.event_log")


class EventLogger:
    """Manages log and telemetry queues with bounded capacity and DLQ overflow."""

    def __init__(self, log_maxsize: int = 100, telemetry_maxsize: int = 1000):
        self.log_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=log_maxsize)
        self.telemetry_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=telemetry_maxsize)
        # Fan-out subscribers for SSE consumers. Each subscriber gets its own queue
        # so events are broadcast (not consumed by only one client).
        self._log_subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._telemetry_subscribers: set[asyncio.Queue[dict[str, Any]]] = set()

    @staticmethod
    def _fanout(subscribers: set[asyncio.Queue[dict[str, Any]]], item: dict[str, Any]) -> None:
        """Best-effort non-blocking broadcast to subscriber queues.

        If a subscriber queue is full, drop its oldest message to keep the
        newest data flowing and avoid blocking the hot path.
        """
        for q in list(subscribers):
            try:
                q.put_nowait(item)
            except asyncio.QueueFull:
                try:
                    _ = q.get_nowait()
                    q.put_nowait(item)
                except Exception:
                    continue
            except Exception:
                continue

    def subscribe_logs(self, maxsize: int = 200) -> asyncio.Queue[dict[str, Any]]:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=maxsize)
        self._log_subscribers.add(q)
        return q

    def unsubscribe_logs(self, q: asyncio.Queue[dict[str, Any]]) -> None:
        self._log_subscribers.discard(q)

    def subscribe_telemetry(self, maxsize: int = 200) -> asyncio.Queue[dict[str, Any]]:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=maxsize)
        self._telemetry_subscribers.add(q)
        return q

    def unsubscribe_telemetry(self, q: asyncio.Queue[dict[str, Any]]) -> None:
        self._telemetry_subscribers.discard(q)

    async def add_log(self, message: str, level: str = "INFO",
                      metadata: dict | None = None, trace_id: str | None = None):
        entry: Dict[str, Any] = {
            "timestamp": time.strftime("%H:%M:%S"),
            "level": level,
            "message": message,
            "metadata": metadata or {},
        }
        if trace_id:
            entry["trace_id"] = trace_id
        if self.log_queue.full():
            dropped = self.log_queue.get_nowait()
            self._schedule_dlq_write(dropped)
        await self.log_queue.put(entry)
        self._fanout(self._log_subscribers, entry)

    async def broadcast_event(self, event_type: str, data: Dict[str, Any]):
        event = {"type": event_type, "timestamp": datetime.now().isoformat(), "data": data}
        if self.telemetry_queue.full():
            dropped = self.telemetry_queue.get_nowait()
            self._schedule_dlq_write(dropped)
        await self.telemetry_queue.put(event)
        self._fanout(self._telemetry_subscribers, event)

    _DLQ_PATH = "dlq.jsonl"
    _DLQ_MAX_BYTES = 10 * 1024 * 1024  # 10 MB — rotate when exceeded

    def _schedule_dlq_write(self, entry: Any) -> None:
        """Write dropped entries off-loop to avoid blocking request processing."""
        try:
            asyncio.create_task(asyncio.to_thread(self._dlq_write_sync, entry))
        except Exception:
            # If scheduling fails, fallback to sync best-effort write.
            self._dlq_write_sync(entry)

    def _dlq_write_sync(self, entry: Any):
        """Dead-letter queue: persist dropped log/telemetry entries to file.
        Non-blocking, best-effort -- prevents silent data loss under load spikes.
        Rotates the file when it exceeds _DLQ_MAX_BYTES to prevent unbounded growth."""
        import os
        try:
            # Rotate if oversized
            try:
                if os.path.getsize(self._DLQ_PATH) > self._DLQ_MAX_BYTES:
                    rotated = self._DLQ_PATH + ".1"
                    if os.path.exists(rotated):
                        os.remove(rotated)
                    os.rename(self._DLQ_PATH, rotated)
            except OSError:
                pass  # File may not exist yet
            with open(self._DLQ_PATH, "a") as f:
                f.write(json.dumps(entry, default=str) + "\n")
        except Exception:
            pass  # DLQ is best-effort, never block the hot path

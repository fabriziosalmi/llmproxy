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
            self._dlq_write(dropped)
        await self.log_queue.put(entry)

    async def broadcast_event(self, event_type: str, data: Dict[str, Any]):
        event = {"type": event_type, "timestamp": datetime.now().isoformat(), "data": data}
        if self.telemetry_queue.full():
            dropped = self.telemetry_queue.get_nowait()
            self._dlq_write(dropped)
        await self.telemetry_queue.put(event)

    _DLQ_PATH = "dlq.jsonl"
    _DLQ_MAX_BYTES = 10 * 1024 * 1024  # 10 MB — rotate when exceeded

    def _dlq_write(self, entry: Any):
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

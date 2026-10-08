"""Turning stored endpoint rows back into LLMEndpoint, for both backends.

The mapping was written out four times (get_by_status and get_all, in each
store), and none of the copies tolerated a bad row: one endpoint whose
``metadata`` was NULL or not JSON, whose URL no longer validated, or whose status
fell outside the enum made the whole read raise. ``get_pool`` runs on every
routed request, so one damaged row took the entire registry out: every request
failed with no endpoint, because of an endpoint that was not even the one wanted.
A row that cannot be read is now skipped and named in the log.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Sequence
from typing import Any

from models import EndpointStatus, LLMEndpoint

logger = logging.getLogger("llmproxy.store")


def endpoint_from_row(row: Sequence[Any]) -> LLMEndpoint | None:
    """``(id, url, status, metadata, latency_ms, success_rate)`` -> LLMEndpoint.

    None, with a warning, when the row cannot be read.
    """
    try:
        return LLMEndpoint(
            id=row[0],
            url=row[1],
            status=EndpointStatus(int(row[2])),
            metadata=json.loads(row[3]) if row[3] else {},
            latency_ms=row[4],
            success_rate=row[5],
        )
    except (ValueError, TypeError, KeyError) as exc:  # pydantic's error is a ValueError
        logger.warning(
            "Skipping unreadable endpoint row %r: %s: %s",
            row[0] if row else None,
            type(exc).__name__,
            exc,
        )
        return None


def endpoints_from_rows(rows: Iterable[Sequence[Any]]) -> list[LLMEndpoint]:
    return [ep for ep in map(endpoint_from_row, rows) if ep is not None]

"""Carry the request identifier into every log record, without touching the
277 call sites that emit them.

A request identifier is minted at the pipeline boundary, returned to the
caller as X-LLMProxy-Request-Id and written into the audit row — so metrics
and the ledger can be correlated to one request. None of the 277 logger calls
in core/ and proxy/ carried it, including the one that fires when the security
shield blocks a request, where the identifier is already in scope twenty-eight
lines above. During an incident that is the wrong way round: the channel an
operator opens first is the one with no join key, so a log line showing a
block cannot be tied to the audit row recording what it cost, nor to the
identifier a user quotes in a complaint.

Binding it per request in a ContextVar and attaching it through a logging
filter means every existing call site gains the field, and every future one
inherits it. A ContextVar is the right container because asyncio tasks copy
the context at creation: a background task spawned from a request keeps that
request's identifier instead of picking up whatever ran last.

Records emitted outside a request — startup, background loops, shutdown —
carry "-", so the field is always present and log parsers do not have to cope
with it sometimes being absent.
"""

from __future__ import annotations

import contextvars
import logging

#: Identifier of the request being served on this task, or "-" outside one.
_request_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "llmproxy_request_id", default="-"
)

NO_REQUEST = "-"


def set_request_id(req_id: str | None) -> contextvars.Token:
    """Bind `req_id` for the current context. Returns a token for reset()."""
    return _request_id.set(req_id or NO_REQUEST)

def get_request_id() -> str:
    """The identifier bound to this context, or "-" outside a request."""
    return _request_id.get()


def reset_request_id(token: contextvars.Token) -> None:
    """Restore whatever was bound before the matching set_request_id()."""
    _request_id.reset(token)


class RequestIdFilter(logging.Filter):
    """Attach the bound identifier to a record as `request_id`.

    Kept for handlers that want it explicitly; `install()` uses the record
    factory instead, which covers records this filter would never see.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "request_id"):
            record.request_id = get_request_id()
        return True


_installed = False


def install() -> None:
    """Ensure every LogRecord carries `request_id`, whoever creates it.

    A record factory rather than a handler filter, because a filter only sees
    records reaching the handlers it is attached to. uvicorn installs its own
    handlers after application start, and any library may add more later; a
    record they emit would then lack the attribute, and a format string
    referencing %(request_id)s would fail to render it — turning a logging
    improvement into logging errors. The factory runs for every record ever
    constructed, so the attribute is always present.

    Idempotent: installing twice does not chain factories.
    """
    global _installed
    if _installed:
        return

    previous = logging.getLogRecordFactory()

    def _factory(*args, **kwargs):
        record = previous(*args, **kwargs)
        if not hasattr(record, "request_id"):
            record.request_id = get_request_id()
        return record

    logging.setLogRecordFactory(_factory)
    _installed = True

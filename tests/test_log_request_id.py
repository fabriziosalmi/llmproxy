"""Every log record carries the request identifier.

The identifier was minted at the pipeline boundary, returned as
X-LLMProxy-Request-Id and written into the audit row — but none of the 277
logger calls in core/ and proxy/ included it, including the security-shield
block, where it is in scope. So metrics and the ledger could be correlated to
a request and the logs could not, which inverts the priority during an
incident: the first thing an operator opens is the channel with no join key.
"""

import asyncio
import logging

import pytest

from core.log_context import (
    NO_REQUEST,
    get_request_id,
    install,
    reset_request_id,
    set_request_id,
)


@pytest.fixture(autouse=True)
def _installed():
    install()  # idempotent
    yield
    # leave no binding behind for the next test
    set_request_id(None)


def test_outside_a_request_the_field_is_present_and_neutral(caplog):
    """Absent is worse than neutral: parsers must not meet a missing key."""
    with caplog.at_level(logging.INFO):
        logging.getLogger("t").info("startup")
    assert getattr(caplog.records[-1], "request_id", None) == NO_REQUEST


def test_a_bound_id_reaches_a_record_nobody_edited(caplog):
    """The whole point: existing call sites gain the field untouched."""
    token = set_request_id("a1b2c3d4e5f60718")
    try:
        with caplog.at_level(logging.WARNING):
            logging.getLogger("llmproxy.request_pipeline").warning(
                "SecurityShield blocked: prompt injection"
            )
    finally:
        reset_request_id(token)
    rec = caplog.records[-1]
    assert rec.request_id == "a1b2c3d4e5f60718"
    assert "SecurityShield blocked" in rec.message


def test_reset_restores_the_previous_binding():
    outer = set_request_id("outer-id")
    inner = set_request_id("inner-id")
    assert get_request_id() == "inner-id"
    reset_request_id(inner)
    assert get_request_id() == "outer-id"
    reset_request_id(outer)
    assert get_request_id() == NO_REQUEST


@pytest.mark.asyncio
async def test_a_spawned_task_keeps_the_id_of_the_request_that_spawned_it():
    """asyncio copies the context at task creation.

    This is why a ContextVar and not a global: the audit and spend writes are
    spawned as background tasks from the request, and must be attributable to
    it rather than to whatever the loop happens to run next.
    """
    seen = {}

    async def background():
        await asyncio.sleep(0)
        seen["inside"] = get_request_id()

    token = set_request_id("req-of-the-spawner")
    task = asyncio.create_task(background())
    reset_request_id(token)  # the request finishes before the task does
    await task

    assert seen["inside"] == "req-of-the-spawner"
    assert get_request_id() == NO_REQUEST


@pytest.mark.asyncio
async def test_concurrent_requests_do_not_see_each_other_ids():
    """Two requests in flight must not cross-label each other's records."""
    results = {}

    async def one(name, req_id):
        token = set_request_id(req_id)
        try:
            await asyncio.sleep(0)
            results[name] = get_request_id()
        finally:
            reset_request_id(token)

    await asyncio.gather(
        one("a", "id-aaaa"), one("b", "id-bbbb"), one("c", "id-cccc")
    )
    assert results == {"a": "id-aaaa", "b": "id-bbbb", "c": "id-cccc"}


def test_a_handler_added_after_install_still_gets_the_field():
    """uvicorn adds handlers after startup; a filter would miss those."""
    records = []

    class _Capture(logging.Handler):
        def emit(self, record):
            records.append(record)

    log = logging.getLogger("added.later")
    handler = _Capture()
    log.addHandler(handler)
    try:
        token = set_request_id("late-handler-id")
        try:
            log.error("emitted through a handler installed afterwards")
        finally:
            reset_request_id(token)
    finally:
        log.removeHandler(handler)

    assert records and records[-1].request_id == "late-handler-id"


def test_install_is_idempotent():
    """Called from both startup and a test fixture without stacking."""
    install()
    install()
    token = set_request_id("still-works")
    try:
        assert get_request_id() == "still-works"
    finally:
        reset_request_id(token)

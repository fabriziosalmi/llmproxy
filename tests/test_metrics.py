"""Tests for core.metrics.MetricsTracker."""

import pytest
from core.metrics import (
    MetricsTracker,
    REQUEST_COUNT,
    REQUEST_ERRORS,
    INJECTION_BLOCKED,
    BUDGET_CONSUMED,
    BUDGET_LIMIT,
    CIRCUIT_OPEN,
)


def test_track_request_increments_counter():
    before = REQUEST_COUNT.labels(
        method="POST", endpoint="/v1/test", http_status=200
    )._value.get()
    MetricsTracker.track_request("POST", "/v1/test", 200, 0.5)
    after = REQUEST_COUNT.labels(
        method="POST", endpoint="/v1/test", http_status=200
    )._value.get()
    assert after > before


def test_track_request_error_class():
    # REQUEST_ERRORS labels are (endpoint, error_class)
    before = REQUEST_ERRORS.labels(
        endpoint="/v1/test_err", error_class="server_error"
    )._value.get()
    MetricsTracker.track_request("POST", "/v1/test_err", 500, 0.1)
    after = REQUEST_ERRORS.labels(
        endpoint="/v1/test_err", error_class="server_error"
    )._value.get()
    assert after > before


def test_track_injection_blocked():
    before = INJECTION_BLOCKED._value.get()
    MetricsTracker.track_injection_blocked()
    after = INJECTION_BLOCKED._value.get()
    assert after > before


def test_set_budget():
    MetricsTracker.set_budget(50.0, 1000.0)
    assert BUDGET_CONSUMED._value.get() == 50.0
    assert BUDGET_LIMIT._value.get() == 1000.0


def test_set_circuit_state():
    MetricsTracker.set_circuit_state("ep_test", True)
    assert CIRCUIT_OPEN.labels(endpoint="ep_test")._value.get() == 1.0
    MetricsTracker.set_circuit_state("ep_test", False)
    assert CIRCUIT_OPEN.labels(endpoint="ep_test")._value.get() == 0.0

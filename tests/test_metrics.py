"""Tests for core.metrics.MetricsTracker."""

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


# ── every declared metric must have something that writes it ────────────────


def _declared_metrics():
    """Module-level Prometheus collectors in core.metrics, by symbol name."""
    import core.metrics as m
    from prometheus_client import Counter, Gauge, Histogram

    return {
        name: obj
        for name, obj in vars(m).items()
        if isinstance(obj, (Counter, Gauge, Histogram))
    }


def test_no_metric_is_declared_without_a_writer():
    """A metric nobody sets reads zero forever — worse than absent.

    llm_proxy_active_agents was declared and had no setter at all; a dashboard
    or alert built on it would sit at zero and look healthy. Deleting it is
    honest; the same check now stops another one from being added.
    """
    import inspect

    import core.metrics as m

    tracker_src = inspect.getsource(m.MetricsTracker)
    module_src = inspect.getsource(m)
    # Ignore the declaration itself when looking for a write.
    for name in _declared_metrics():
        writes = tracker_src.count(name) or (
            module_src.count(name) - 1  # minus the declaration
        )
        assert writes > 0, (
            f"{name} is declared in core/metrics.py and never written — "
            f"either wire it up or delete it"
        )


def test_every_tracker_method_is_called_by_the_application():
    """A setter nobody calls is the same dead metric, one level down.

    MetricsTracker.set_roi existed and llm_proxy_roi_efficiency was exported,
    but nothing in the proxy ever called it — so the metric was published and
    never changed. set_pool_size was in the same state until /metrics started
    refreshing the gauge.
    """
    import pathlib

    import core.metrics as m

    root = pathlib.Path(__file__).resolve().parent.parent
    sources = []
    for path in root.rglob("*.py"):
        rel = path.relative_to(root)
        parts = rel.parts
        if parts[0] in {"tests", "node_modules", ".venv", "venv", "build"}:
            continue
        if rel == pathlib.Path("core/metrics.py"):
            continue
        sources.append(path.read_text(errors="ignore"))
    corpus = "\n".join(sources)

    methods = [
        name
        for name, obj in vars(m.MetricsTracker).items()
        if isinstance(obj, staticmethod) and not name.startswith("_")
    ]
    assert methods, "MetricsTracker exposes no static methods — check the introspection"
    dead = [name for name in methods if f"MetricsTracker.{name}(" not in corpus]
    assert not dead, (
        f"MetricsTracker methods nothing calls: {dead} — the metrics behind "
        f"them are published and never change"
    )

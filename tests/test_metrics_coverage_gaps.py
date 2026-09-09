"""Metrics that existed but described a subset of what happened.

Three separate gaps, all with the same shape — a counter incremented by hand
inside the handlers that remembered:

  * llm_proxy_requests_total was raised in chat and embeddings only, so
    /v1/completions served traffic that appeared in neither it nor the latency
    histogram, and the dashboard's throughput figure (a sum over that counter)
    under-reported against the spend ledger;
  * llm_proxy_auth_failures_total was raised in two data-plane handlers and
    nowhere in the middleware that rejects on all 65 control-plane routes, so
    key enumeration against /api/v1/registry moved nothing;
  * llm_proxy_budget_consumed_usd was set in the chat route alone, so spend
    through streaming, completions and embeddings updated the ledger without
    updating the series monitoring/prometheus-rules.yml warns and pages on.

Plus one that let a caller mint Prometheus series, and one that gives the
background loops a way to say they are alive.
"""

import asyncio

import httpx
import pytest

from conftest import InMemoryRepository, minimal_config
from test_e2e import LightweightAgent


def _sample(metric, **labels):
    """Current value of a labelled child, or None when it has never been set."""
    for m in metric.collect():
        for s in m.samples:
            if all(s.labels.get(k) == v for k, v in labels.items()):
                return s.value
    return None


def _open_agent():
    from proxy.app_factory import create_app

    config = minimal_config()
    config["server"]["auth"]["enabled"] = False
    agent = LightweightAgent(InMemoryRepository(), config)
    agent.app = create_app(agent)
    return agent


# ── request accounting happens for every route, not two ─────────────────────


@pytest.mark.asyncio
async def test_every_route_is_counted_including_the_control_plane():
    """The counter was raised in chat and embeddings only."""
    from core.metrics import REQUEST_COUNT

    agent = _open_agent()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=agent.app), base_url="http://test"
    ) as c:
        await c.get("/api/v1/registry")

    assert (
        _sample(
            REQUEST_COUNT,
            method="GET",
            endpoint="/api/v1/registry",
            http_status="200",
        )
        is not None
    ), "a control-plane read produced no request counter sample"


@pytest.mark.asyncio
async def test_the_label_is_the_route_template_not_the_raw_path():
    """/v1/models/{model_id:path} would otherwise mint a series per model."""
    from core.metrics import REQUEST_COUNT

    agent = _open_agent()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=agent.app), base_url="http://test"
    ) as c:
        await c.get("/v1/models/some-model-nobody-configured")

    assert (
        _sample(REQUEST_COUNT, endpoint="/v1/models/{model_id:path}") is not None
    ), "the raw path was used as a label — unbounded cardinality"


@pytest.mark.asyncio
async def test_latency_is_observed_for_a_route_no_handler_instrumented():
    from core.metrics import REQUEST_LATENCY

    agent = _open_agent()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=agent.app), base_url="http://test"
    ) as c:
        await c.get("/health")

    assert _sample(REQUEST_LATENCY, endpoint="/health") is not None


# ── control-plane rejections reach the auth-failure counter ─────────────────


@pytest.mark.asyncio
async def test_a_control_plane_rejection_increments_the_auth_counter(monkeypatch):
    """Key enumeration against /api/v1/* moved no counter at all."""
    import os

    from core.infisical import clear_cache
    from core.metrics import AUTH_FAILURES
    from proxy.app_factory import create_app

    os.environ["LLM_PROXY_API_KEYS"] = "sk-proxy-real"
    clear_cache()

    config = minimal_config()
    config["server"]["auth"]["enabled"] = True
    config["server"]["auth"]["api_keys_env"] = "LLM_PROXY_API_KEYS"
    agent = LightweightAgent(InMemoryRepository(), config)
    agent.app = create_app(agent)

    before = _sample(AUTH_FAILURES, reason="control_plane_bad_key") or 0.0
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=agent.app), base_url="http://test"
    ) as c:
        resp = await c.get(
            "/api/v1/registry", headers={"Authorization": "Bearer sk-guessed"}
        )
    after = _sample(AUTH_FAILURES, reason="control_plane_bad_key") or 0.0

    assert resp.status_code == 401
    assert after == before + 1


@pytest.mark.asyncio
async def test_an_anonymous_control_plane_call_is_counted_separately(monkeypatch):
    """"No key" and "wrong key" are different alerts."""
    import os

    from core.infisical import clear_cache
    from core.metrics import AUTH_FAILURES
    from proxy.app_factory import create_app

    os.environ["LLM_PROXY_API_KEYS"] = "sk-proxy-real"
    clear_cache()

    config = minimal_config()
    config["server"]["auth"]["enabled"] = True
    config["server"]["auth"]["api_keys_env"] = "LLM_PROXY_API_KEYS"
    agent = LightweightAgent(InMemoryRepository(), config)
    agent.app = create_app(agent)

    before = _sample(AUTH_FAILURES, reason="control_plane_no_key") or 0.0
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=agent.app), base_url="http://test"
    ) as c:
        await c.get("/api/v1/registry")
    after = _sample(AUTH_FAILURES, reason="control_plane_no_key") or 0.0

    assert after == before + 1


# ── the budget gauge follows every charging site ────────────────────────────


@pytest.mark.asyncio
async def test_charging_through_any_route_moves_the_budget_gauge():
    """It was set in the chat route only.

    charge_and_persist is the funnel every charging site uses — streaming in
    the forwarder, embeddings, and the chat pipeline — so the gauge belongs
    here rather than in whichever handler remembered.
    """
    from core.metrics import BUDGET_CONSUMED, BUDGET_LIMIT
    from proxy.budget import charge_and_persist

    class _Rotator:
        total_cost_today = 0.0
        config = {"budget": {"daily_limit": 50.0}}

        def enqueue_write(self, key, value):
            pass

    rotator = _Rotator()
    await charge_and_persist(rotator, asyncio.Lock(), 1.25)

    assert _sample(BUDGET_CONSUMED) == pytest.approx(1.25)
    assert _sample(BUDGET_LIMIT) == pytest.approx(50.0)


@pytest.mark.asyncio
async def test_a_gauge_failure_never_breaks_a_charge():
    """Telemetry must not be able to fail a request that spent money."""
    from proxy.budget import charge_and_persist

    class _Rotator:
        total_cost_today = 0.0
        config = None  # forces the .get() chain to fall over

        def enqueue_write(self, key, value):
            pass

    rotator = _Rotator()
    await charge_and_persist(rotator, asyncio.Lock(), 0.5)

    assert rotator.total_cost_today == pytest.approx(0.5)


# ── the cost label cannot be minted by a caller ─────────────────────────────


def test_an_unknown_model_collapses_to_other():
    """prometheus_client never evicts label children."""
    from core.metrics import ESTIMATED_COST, MetricsTracker

    MetricsTracker.track_usage(
        endpoint="/v1/chat/completions",
        model="../../etc/passwd-or-any-string-a-caller-chose",
        prompt_tokens=1,
        completion_tokens=1,
        cost=0.01,
        known_models=frozenset({"gpt-4o"}),
    )

    assert (
        _sample(
            ESTIMATED_COST,
            endpoint="/v1/chat/completions",
            model="../../etc/passwd-or-any-string-a-caller-chose",
        )
        is None
    )
    assert _sample(ESTIMATED_COST, endpoint="/v1/chat/completions", model="other")


def test_a_known_model_keeps_its_name():
    """Bounding cardinality must not destroy the breakdown the counter is for."""
    from core.metrics import ESTIMATED_COST, MetricsTracker

    MetricsTracker.track_usage(
        endpoint="/v1/chat/completions",
        model="gpt-4o",
        prompt_tokens=1,
        completion_tokens=1,
        cost=0.02,
        known_models=frozenset({"gpt-4o"}),
    )

    assert _sample(ESTIMATED_COST, endpoint="/v1/chat/completions", model="gpt-4o")


def test_known_model_names_reads_all_three_declarations():
    from core.model_resolver import known_model_names

    names = known_model_names(
        {
            "endpoints": {"openai": {"models": ["gpt-4o"]}},
            "model_aliases": {"fast": "gpt-4o-mini"},
            "model_groups": {"auto": {"models": [{"model": "gemini-2.5-flash"}]}},
        }
    )

    assert {"gpt-4o", "fast", "gpt-4o-mini", "auto", "gemini-2.5-flash"} <= names


# ── background loops can say they are alive ─────────────────────────────────


def test_every_loop_reports_a_successful_iteration():
    """A stopped loop was indistinguishable from a working one."""
    import inspect

    import proxy.background as background

    source = inspect.getsource(background)
    loops = [
        name
        for name, obj in vars(background).items()
        if name.endswith("_loop") and inspect.iscoroutinefunction(obj)
    ]
    assert loops, "no loops discovered — this test would be vacuous"

    missing = [
        loop
        for loop in loops
        if f'_iteration_ok("{loop.removesuffix("_loop")}")' not in source
    ]
    assert not missing, f"loops with no liveness signal: {missing}"


def test_the_heartbeat_records_a_timestamp():
    import time

    from core.metrics import BACKGROUND_LAST_SUCCESS, MetricsTracker

    MetricsTracker.mark_background_iteration("retention_purge")
    value = _sample(BACKGROUND_LAST_SUCCESS, loop="retention_purge")

    assert value is not None
    assert abs(value - time.time()) < 5


def test_the_heartbeat_cannot_kill_a_loop(monkeypatch):
    """Telemetry failing must never take a background task with it."""
    import core.metrics as metrics

    from proxy.background import _iteration_ok

    def _boom(loop):
        raise RuntimeError("registry exploded")

    monkeypatch.setattr(metrics.MetricsTracker, "mark_background_iteration", _boom)
    _iteration_ok("config_watch")  # must not raise

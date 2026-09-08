"""What the forwarder does when every attempt fails.

proxy/forwarder.py was at 51% statement coverage — the lowest of the three
files that decide whether a request is authenticated, where it goes and what
it costs. The fallback walk is its core: attempts are the primary endpoint
plus whatever fallback_chains lists for the model, tried in order, with the
circuit breaker consulted before each. The branches nothing exercised were
the ones that matter during an incident: every endpoint circuit-open, and the
chain running out.

These tests drive that walk directly rather than through a live upstream, so
they assert the decisions rather than the network.
"""

import asyncio

import pytest
from fastapi import HTTPException

from proxy.forwarder import RequestForwarder


class _Breaker:
    def __init__(self, allowed: bool):
        self._allowed = allowed
        self.successes = 0
        self.failures = 0

    async def can_execute(self):
        return self._allowed

    async def report_success(self):
        self.successes += 1

    async def report_failure(self):
        self.failures += 1


class _CircuitManager:
    """Every endpoint open or closed, as the test chooses."""

    def __init__(self, allowed: bool):
        self._breaker = _Breaker(allowed)

    async def get_breaker(self, endpoint_id):
        return self._breaker

    async def get_all_states(self):
        return {}


class _Endpoint:
    def __init__(self, eid="ep-primary", provider="openai"):
        self.id = eid
        self.url = f"http://{eid}.invalid/v1"
        self.provider = provider
        self.provider_type = provider


class _Ctx:
    def __init__(self, model="gpt-4o"):
        self.body = {"model": model, "messages": [{"role": "user", "content": "hi"}]}
        self.metadata = {}
        self.response = None
        self.session_id = "s1"


def _forwarder(config, *, circuits_allowed=True):
    return RequestForwarder(
        config=config,
        circuit_manager=_CircuitManager(circuits_allowed),
        budget_lock=asyncio.Lock(),
        get_session=lambda: asyncio.sleep(0),
        add_log=lambda *a, **k: asyncio.sleep(0),
        security=None,
    )


# ── nothing to try ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_no_routable_endpoint_is_503_not_a_crash():
    """target None and no fallback chain: refuse cleanly."""
    fwd = _forwarder({"fallback_chains": {}})
    with pytest.raises(HTTPException) as exc:
        await fwd.forward_with_fallback(
            _Ctx(), None, {}, session=None, cost_ref={"delta": 0.0}
        )
    assert exc.value.status_code in (502, 503), (
        f"expected a refusal, got {exc.value.status_code}"
    )


# ── every circuit open ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_all_circuits_open_surfaces_a_refusal_not_a_silent_pass():
    """The branch that fires when an endpoint is gated and has no fallback.

    A circuit-open primary with an empty chain must fail the request. The
    danger it guards against is the opposite: continuing as though a gated
    endpoint had answered.
    """
    fwd = _forwarder({"fallback_chains": {}}, circuits_allowed=False)
    ctx = _Ctx()
    with pytest.raises(HTTPException) as exc:
        await fwd.forward_with_fallback(
            ctx, _Endpoint(), {}, session=None, cost_ref={"delta": 0.0}
        )
    assert exc.value.status_code in (502, 503)
    assert ctx.response is None, "a gated endpoint must not leave a response behind"


# ── the model is restored ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_original_model_is_restored_after_exhaustion():
    """The walk rewrites ctx.body['model'] per attempt.

    If it did not restore the caller's value on the way out, the model the
    client asked for would be lost — and the audit row written afterwards
    would record whichever fallback was tried last, not what was requested.
    """
    fwd = _forwarder({"fallback_chains": {}}, circuits_allowed=False)
    ctx = _Ctx(model="gpt-4o")
    with pytest.raises(HTTPException):
        await fwd.forward_with_fallback(
            ctx, _Endpoint(), {}, session=None, cost_ref={"delta": 0.0}
        )
    assert ctx.body["model"] == "gpt-4o", (
        "the caller's model must survive the fallback walk"
    )


# ── budget saturation refuses before any attempt ────────────────────────────


@pytest.mark.asyncio
async def test_budget_saturation_refuses_with_402_before_trying_anything():
    """Refusing beats silently downgrading: the code says so, this pins it."""
    fwd = _forwarder({"fallback_chains": {}})
    ctx = _Ctx()
    ctx.metadata["_budget_saturated"] = True
    with pytest.raises(HTTPException) as exc:
        await fwd.forward_with_fallback(
            ctx, _Endpoint(), {}, session=None, cost_ref={"delta": 0.0}
        )
    assert exc.value.status_code == 402
    assert "Budget" in str(exc.value.detail)


@pytest.mark.asyncio
async def test_a_saturated_budget_is_checked_before_the_circuit_breaker():
    """Order matters: a saturated budget must not depend on endpoint health."""
    fwd = _forwarder({"fallback_chains": {}}, circuits_allowed=False)
    ctx = _Ctx()
    ctx.metadata["_budget_saturated"] = True
    with pytest.raises(HTTPException) as exc:
        await fwd.forward_with_fallback(
            ctx, _Endpoint(), {}, session=None, cost_ref={"delta": 0.0}
        )
    assert exc.value.status_code == 402, (
        "budget saturation must win over circuit state, so the reason a caller "
        "is refused does not depend on unrelated endpoint health"
    )

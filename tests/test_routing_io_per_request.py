"""Routing paid one network round trip per registered endpoint, per request.

select_endpoint ran on Ring 3 for every proxied request and awaited
`get_breaker(e.id).can_execute()` for each endpoint in the pool, serially. With
Redis configured each of those is an evalsha, so pre-upstream latency grew
linearly with a number the operator sets by registering endpoints — against a
deterministic security pipeline the project's own benchmarks put at tens of
microseconds.

`can_execute` is also not a read. Its Lua script SETS the half-open probe key
when the recovery timeout has elapsed, so probing every candidate spent the
single recovery probe on endpoints that were never going to be chosen — and
/health and the dashboard summary ran the same loop on every poll.
"""

import asyncio

import pytest

from core.circuit_breaker import CircuitManager


class _RecordingRedis:
    """Counts round trips so the fan-out is a number, not an impression."""

    def __init__(self, values=None):
        self.mget_calls = 0
        self.keys_seen: list = []
        self._values = values or {}

    async def mget(self, *keys):
        self.mget_calls += 1
        self.keys_seen.extend(keys)
        return [self._values.get(k) for k in keys]

    async def script_load(self, script):
        return "sha"

    async def evalsha(self, *args, **kwargs):  # pragma: no cover
        raise AssertionError(
            "filter_executable must not run the state-transition script — "
            "it mutates the half-open probe key"
        )


def _manager(redis_client, **cfg):
    return CircuitManager(
        redis_client=redis_client,
        config={"circuit_breaker": cfg} if cfg else None,
    )


# ── one round trip, whatever the pool size ──────────────────────────────────


@pytest.mark.asyncio
async def test_a_pool_of_twenty_costs_one_round_trip():
    redis = _RecordingRedis()
    manager = _manager(redis)

    result = await manager.filter_executable([f"ep-{i}" for i in range(20)])

    assert redis.mget_calls == 1, (
        f"{redis.mget_calls} round trips for 20 endpoints — the fan-out is back"
    )
    assert len(result) == 20  # no state recorded means closed


@pytest.mark.asyncio
async def test_an_empty_pool_costs_nothing():
    redis = _RecordingRedis()
    manager = _manager(redis)

    assert await manager.filter_executable([]) == set()
    assert redis.mget_calls == 0


@pytest.mark.asyncio
async def test_it_reads_state_rather_than_requesting_permission():
    """_RecordingRedis.evalsha raises: running the script would mutate."""
    redis = _RecordingRedis()
    manager = _manager(redis)

    await manager.filter_executable(["ep-a"])

    assert "cb:ep-a:state" in redis.keys_seen
    assert "cb:ep-a:last" in redis.keys_seen


# ── the verdict matches what can_execute would have said ────────────────────


@pytest.mark.asyncio
async def test_a_closed_circuit_is_executable():
    redis = _RecordingRedis({"cb:ep-a:state": "closed"})
    assert await _manager(redis).filter_executable(["ep-a"]) == {"ep-a"}


@pytest.mark.asyncio
async def test_a_recently_opened_circuit_is_not():
    import time

    redis = _RecordingRedis(
        {"cb:ep-a:state": "open", "cb:ep-a:last": str(time.time())}
    )
    assert await _manager(redis).filter_executable(["ep-a"]) == set()


@pytest.mark.asyncio
async def test_an_open_circuit_past_its_recovery_timeout_is_admitted():
    """Otherwise nothing would ever recover: this is what lets the forwarder's
    own can_execute promote it to half-open and spend the probe."""
    import time

    redis = _RecordingRedis(
        {"cb:ep-a:state": "open", "cb:ep-a:last": str(time.time() - 999)}
    )
    manager = _manager(redis, recovery_timeout=60)

    assert await manager.filter_executable(["ep-a"]) == {"ep-a"}


@pytest.mark.asyncio
async def test_a_half_open_circuit_is_admitted():
    redis = _RecordingRedis({"cb:ep-a:state": "half_open"})
    assert await _manager(redis).filter_executable(["ep-a"]) == {"ep-a"}


@pytest.mark.asyncio
async def test_a_mixed_pool_is_partitioned_correctly():
    import time

    now = time.time()
    redis = _RecordingRedis(
        {
            "cb:open-recent:state": "open",
            "cb:open-recent:last": str(now),
            "cb:open-stale:state": "open",
            "cb:open-stale:last": str(now - 999),
            "cb:closed:state": "closed",
            "cb:half:state": "half_open",
        }
    )
    manager = _manager(redis, recovery_timeout=60)

    result = await manager.filter_executable(
        ["open-recent", "open-stale", "closed", "half", "unknown"]
    )

    assert result == {"open-stale", "closed", "half", "unknown"}
    assert redis.mget_calls == 1


# ── degradation is unchanged ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_redis_failure_falls_back_rather_than_blanking_the_pool():
    """A Redis problem must not make every endpoint look unavailable."""

    class _BrokenRedis(_RecordingRedis):
        async def mget(self, *keys):
            raise ConnectionError("redis is down")

    manager = _manager(_BrokenRedis())
    result = await manager.filter_executable(["ep-a", "ep-b"])

    assert result == {"ep-a", "ep-b"}, "fell back to closed-circuit-blocks-all"


@pytest.mark.asyncio
async def test_without_redis_it_uses_the_in_process_breakers():
    manager = CircuitManager()  # no redis_client

    assert await manager.filter_executable(["ep-a"]) == {"ep-a"}


# ── configuration reaches breakers created after startup ────────────────────


@pytest.mark.asyncio
async def test_thresholds_from_config_reach_a_new_breaker():
    """A breaker created after boot used to get the hard-coded 5/60 defaults
    until the config watcher next fired and patched it."""
    manager = CircuitManager(
        config={"circuit_breaker": {"failure_threshold": 9, "recovery_timeout": 11}}
    )
    breaker = await manager.get_breaker("ep-a")

    assert breaker.failure_threshold == 9
    assert breaker.recovery_timeout == 11


# ── the call sites use it ───────────────────────────────────────────────────


def test_no_hot_path_probes_breakers_in_a_loop():
    """The shape, asserted structurally so it cannot creep back."""
    import inspect

    import proxy.routes.admin as admin
    import proxy.routes.telemetry as telemetry

    from plugins.default import smart_router

    for module in (smart_router, telemetry, admin):
        source = inspect.getsource(module)
        assert "get_breaker(e.id)).can_execute()" not in source, (
            f"{module.__name__} probes every endpoint in a loop again"
        )


def test_the_dashboard_builds_the_state_map_once():
    """It called get_all_states twice in one handler and probed separately."""
    import inspect

    import proxy.routes.admin as admin

    summary = inspect.getsource(admin)
    start = summary.index("async def get_dashboard_summary")
    end = summary.index("@router.get", start + 100)
    body = summary[start:end]

    assert body.count("get_all_states()") == 1, (
        "the summary handler recomputes the circuit state map"
    )


@pytest.mark.asyncio
async def test_routing_selects_from_the_batched_verdict():
    """End to end through select_endpoint with a pool of three."""
    from core.plugin_engine import PluginContext

    class _Endpoint:
        def __init__(self, id_):
            self.id = id_
            self.url = f"http://{id_}"
            self.metadata = {}
            self.latency_ms = 10.0
            self.success_rate = 1.0

    class _Store:
        async def get_pool(self):
            return [_Endpoint("a"), _Endpoint("b"), _Endpoint("c")]

    redis = _RecordingRedis({"cb:b:state": "open", "cb:b:last": str(9e12)})

    class _Rotator:
        store = _Store()
        circuit_manager = _manager(redis)
        config: dict = {}
        priority_mode = False
        _endpoint_stats: dict = {}
        cost_weight = 0.0

        async def _add_log(self, *args, **kwargs):
            return None

    ctx = PluginContext(body={"model": "gpt-4o"}, metadata={"rotator": _Rotator()})
    await smart_router_select(ctx)

    assert redis.mget_calls == 1
    assert ctx.error is None


async def smart_router_select(ctx):
    from plugins.default.smart_router import select_endpoint

    return await select_endpoint(ctx)


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(test_a_pool_of_twenty_costs_one_round_trip())

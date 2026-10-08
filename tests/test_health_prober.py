"""EndpointHealthProber: which endpoints it probes, and what a probe does to the breaker.

It feeds circuit breakers and endpoint stats on a timer, so a wrong skip rule means
silent traffic to a third party (or none to a dead endpoint), and a wrong verdict
means a healthy endpoint is gated or a dead one stays in rotation. It had no test.
"""

import asyncio
import logging

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from core import endpoint_stats
from core.health_prober import EndpointHealthProber


class _Breaker:
    def __init__(self, state="closed"):
        self.state = state
        self.calls = []

    async def report_success(self):
        self.calls.append("success")

    async def report_failure(self):
        self.calls.append("failure")


class _Circuits:
    def __init__(self):
        self.breakers = {}

    async def get_breaker(self, name):
        return self.breakers.setdefault(name, _Breaker())


@pytest.fixture
def stats(monkeypatch):
    seen = []

    async def record(name, latency_ms, success, **kw):
        seen.append((name, success))

    monkeypatch.setattr(endpoint_stats, "update_endpoint_stats", record)
    return seen


def _prober(endpoints, session=None):
    async def get_session():
        return session

    from proxy.adapters.registry import get_adapter

    return EndpointHealthProber(
        {"endpoints": endpoints}, _Circuits(), get_session, adapter_for=get_adapter
    )


async def _probed(prober):
    names = []

    async def record(ep_name, provider, base_url, model):
        names.append(ep_name)

    prober._probe_one_jittered = record
    await prober._probe_all()
    return names


# ── what is probed ────────────────────────────────────────────────────────────


async def test_only_complete_remote_endpoints_are_probed():
    prober = _prober(
        {
            "good": {"base_url": "https://api.example.com/v1", "models": ["m"]},
            "no_url": {"models": ["m"]},
            "no_models": {"base_url": "https://api.example.com/v1"},
            "local": {"base_url": "http://localhost:11434/v1", "models": ["m"]},
            "local_opted_in": {
                "base_url": "http://127.0.0.1:11434/v1", "models": ["m"], "probe_local": True,
            },
        }
    )

    assert sorted(await _probed(prober)) == ["good", "local_opted_in"]


async def test_a_template_placeholder_is_skipped_and_announced_once(caplog):
    prober = _prober({"tmpl": {"base_url": "https://{resource}.example.com", "models": ["m"]}})

    with caplog.at_level(logging.INFO, logger="llmproxy.health_prober"):
        assert await _probed(prober) == []
        assert await _probed(prober) == []

    assert sum("Probe skip: tmpl" in r.message for r in caplog.records) == 1


async def test_an_endpoint_whose_breaker_is_open_is_left_to_the_breaker():
    prober = _prober({"down": {"base_url": "https://api.example.com/v1", "models": ["m"]}})
    prober.circuit_manager.breakers["down"] = _Breaker(state="open")

    assert await _probed(prober) == []


async def test_an_endpoint_without_its_provider_key_is_not_probed(monkeypatch, caplog):
    monkeypatch.delenv("PROBE_TEST_KEY", raising=False)
    prober = _prober(
        {"keyed": {"base_url": "https://api.example.com/v1", "models": ["m"],
                   "api_key_env": "PROBE_TEST_KEY"}}
    )

    with caplog.at_level(logging.INFO, logger="llmproxy.health_prober"):
        assert await _probed(prober) == []
        assert await _probed(prober) == []
    assert sum("provider key not set" in r.message for r in caplog.records) == 1

    monkeypatch.setenv("PROBE_TEST_KEY", "sk-real-looking-key-123456")
    assert await _probed(prober) == ["keyed"]


# ── what a probe does ────────────────────────────────────────────────────────


@pytest.fixture
async def upstream():
    behaviour = {"status": 200, "delay": 0.0}

    async def handler(request):
        await asyncio.sleep(behaviour["delay"])
        return web.json_response({"choices": []}, status=behaviour["status"])

    app = web.Application()
    app.router.add_route("POST", "/{tail:.*}", handler)
    server = TestServer(app)
    await server.start_server()
    yield str(server.make_url("")).rstrip("/"), behaviour
    await server.close()


async def test_a_healthy_endpoint_reports_success(upstream, stats):
    base, _ = upstream
    async with aiohttp.ClientSession() as session:
        prober = _prober({"ep": {"base_url": base, "models": ["m"], "provider": "openai"}}, session)
        await prober._probe_one("ep", "openai", base, "m")

    assert prober.circuit_manager.breakers["ep"].calls == ["success"]
    assert stats == [("ep", True)]


@pytest.mark.parametrize("status", [401, 429, 500, 503])
async def test_an_error_status_reports_failure(upstream, stats, status):
    base, behaviour = upstream
    behaviour["status"] = status
    async with aiohttp.ClientSession() as session:
        prober = _prober({"ep": {"base_url": base, "models": ["m"], "provider": "openai"}}, session)
        await prober._probe_one("ep", "openai", base, "m")

    assert prober.circuit_manager.breakers["ep"].calls == ["failure"]
    assert stats == [("ep", False)]


async def test_a_timeout_reports_failure(upstream, stats, monkeypatch):
    base, behaviour = upstream
    behaviour["delay"] = 1.0
    monkeypatch.setattr("core.health_prober.PROBE_TIMEOUT", 0.1)
    async with aiohttp.ClientSession() as session:
        prober = _prober({"ep": {"base_url": base, "models": ["m"], "provider": "openai"}}, session)
        await prober._probe_one("ep", "openai", base, "m")

    assert prober.circuit_manager.breakers["ep"].calls == ["failure"]
    assert stats == [("ep", False)]


async def test_an_unreachable_endpoint_reports_failure(stats):
    async with aiohttp.ClientSession() as session:
        prober = _prober({"ep": {"base_url": "http://127.0.0.1:9", "models": ["m"]}}, session)
        await prober._probe_one("ep", "openai", "http://127.0.0.1:9", "m")

    assert prober.circuit_manager.breakers["ep"].calls == ["failure"]


async def test_only_a_change_of_state_is_logged_loudly(upstream, stats, caplog):
    base, behaviour = upstream
    behaviour["status"] = 503
    async with aiohttp.ClientSession() as session:
        prober = _prober({"ep": {"base_url": base, "models": ["m"], "provider": "openai"}}, session)
        with caplog.at_level(logging.INFO, logger="llmproxy.health_prober"):
            for _ in range(5):
                await prober._probe_one("ep", "openai", base, "m")
            behaviour["status"] = 200
            await prober._probe_one("ep", "openai", base, "m")

    loud = [r for r in caplog.records if r.levelno >= logging.INFO]
    assert [("FAIL" in r.message, "OK" in r.message) for r in loud] == [(True, False), (False, True)]

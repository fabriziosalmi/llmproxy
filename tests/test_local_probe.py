"""Unit tests for core/local_probe.py — local + peer auto-discovery."""

import asyncio
from unittest.mock import patch

import pytest

from core import local_probe


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("LLM_PROXY_DISCOVERY_PEERS", raising=False)
    monkeypatch.delenv("LLM_PROXY_LOCAL_DISCOVERY", raising=False)


# ── _is_enabled ────────────────────────────────────────────────────────────


def test_enabled_by_default():
    assert local_probe._is_enabled({}) is True


def test_env_disable_wins_over_config(monkeypatch):
    monkeypatch.setenv("LLM_PROXY_LOCAL_DISCOVERY", "0")
    assert local_probe._is_enabled({"discovery": {"local_scan": True}}) is False


@pytest.mark.parametrize("val", ["0", "false", "off", "no", "", "False", "OFF"])
def test_env_disable_values(monkeypatch, val):
    monkeypatch.setenv("LLM_PROXY_LOCAL_DISCOVERY", val)
    assert local_probe._is_enabled({}) is False


def test_config_opt_out():
    assert local_probe._is_enabled({"discovery": {"local_scan": False}}) is False


# ── _parse_peers ───────────────────────────────────────────────────────────


def test_parse_peers_empty_returns_empty():
    assert local_probe._parse_peers({}) == []


def test_parse_peers_bare_host(monkeypatch):
    monkeypatch.setenv("LLM_PROXY_DISCOVERY_PEERS", "100.98.112.23")
    assert local_probe._parse_peers({}) == [("100.98.112.23", None)]


def test_parse_peers_host_port(monkeypatch):
    monkeypatch.setenv("LLM_PROXY_DISCOVERY_PEERS", "100.108.97.78:8000")
    assert local_probe._parse_peers({}) == [("100.108.97.78", 8000)]


def test_parse_peers_mixed_list(monkeypatch):
    monkeypatch.setenv(
        "LLM_PROXY_DISCOVERY_PEERS",
        "100.98.112.23, 100.66.12.82:1234 , nas.lan ,  ",
    )
    assert local_probe._parse_peers({}) == [
        ("100.98.112.23", None),
        ("100.66.12.82", 1234),
        ("nas.lan", None),
    ]


def test_parse_peers_skips_bad_port(monkeypatch, caplog):
    monkeypatch.setenv("LLM_PROXY_DISCOVERY_PEERS", "valid.host,bad.host:NaN")
    peers = local_probe._parse_peers({})
    assert ("valid.host", None) in peers
    assert not any(h == "bad.host" for h, _ in peers)


def test_parse_peers_config_fallback():
    cfg = {"discovery": {"peers": ["a.lan", "b.lan:1234"]}}
    assert local_probe._parse_peers(cfg) == [("a.lan", None), ("b.lan", 1234)]


def test_parse_peers_env_wins_over_config(monkeypatch):
    monkeypatch.setenv("LLM_PROXY_DISCOVERY_PEERS", "from-env")
    cfg = {"discovery": {"peers": ["from-config"]}}
    assert local_probe._parse_peers(cfg) == [("from-env", None)]


# ── _unique_id + _url_already_configured ───────────────────────────────────


def test_unique_id_no_collision():
    assert local_probe._unique_id({}, "ollama") == "ollama"


def test_unique_id_collision_adds_auto_suffix():
    assert local_probe._unique_id({"ollama": {}}, "ollama") == "ollama-auto"


def test_unique_id_repeated_collisions_numbered():
    endpoints = {"ollama": {}, "ollama-auto": {}}
    assert local_probe._unique_id(endpoints, "ollama") == "ollama-auto2"
    endpoints["ollama-auto2"] = {}
    assert local_probe._unique_id(endpoints, "ollama") == "ollama-auto3"


def test_url_already_configured_exact_match():
    endpoints = {"a": {"base_url": "http://host:11434/v1"}}
    assert local_probe._url_already_configured(endpoints, "http://host:11434/v1") is True


def test_url_already_configured_trailing_slash_tolerant():
    endpoints = {"a": {"base_url": "http://host:11434/v1/"}}
    assert local_probe._url_already_configured(endpoints, "http://host:11434/v1") is True


def test_url_already_configured_miss():
    endpoints = {"a": {"base_url": "http://host:11434/v1"}}
    assert local_probe._url_already_configured(endpoints, "http://other:11434/v1") is False


# ── discover_local_endpoints (integration, disabled path) ──────────────────


def test_discover_returns_empty_when_disabled(monkeypatch):
    monkeypatch.setenv("LLM_PROXY_LOCAL_DISCOVERY", "0")
    result = asyncio.run(local_probe.discover_local_endpoints({}, timeout=0.1))
    assert result == []


def test_discover_returns_empty_when_no_hosts_resolve(monkeypatch):
    """If every candidate host fails DNS, the probe returns [] cleanly."""
    with patch.object(local_probe, "_host_resolves", return_value=False):
        result = asyncio.run(local_probe.discover_local_endpoints({}, timeout=0.1))
    assert result == []


def test_discover_injects_probed_service_into_config(monkeypatch):
    """Stub _probe_one to return a fake Ollama hit and check the endpoint gets injected."""

    fake_hit = {
        "name": "ollama",
        "provider": "ollama",
        "base_url": "http://host.docker.internal:11434/v1",
        "models": ["llama3.2:3b", "qwen2.5-coder:7b"],
        "host": "host.docker.internal",
    }

    async def fake_probe_one(session, host, probe, timeout, port_override=None):
        # Only return a hit for the ollama port on host.docker.internal; everything else None.
        if host == "host.docker.internal" and probe["port"] == 11434:
            return fake_hit
        return None

    with patch.object(local_probe, "_host_resolves", return_value=True), \
         patch.object(local_probe, "_probe_one", side_effect=fake_probe_one):
        cfg: dict = {"endpoints": {}}
        # Force just two hosts to keep the fan-out predictable.
        monkeypatch.setattr(local_probe, "_LOCAL_PROBE_HOSTS", ("host.docker.internal",))
        injected = asyncio.run(local_probe.discover_local_endpoints(cfg, timeout=0.1))

    assert injected == ["ollama"]
    ep = cfg["endpoints"]["ollama"]
    assert ep["auth_type"] == "none"
    assert ep["_source"] == "auto-discovery"
    assert ep["models"] == fake_hit["models"]

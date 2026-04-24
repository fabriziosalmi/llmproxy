"""Unit tests for core/env_endpoints.py — env-declared OpenAI-compatible endpoints."""

import os

import pytest

from core.env_endpoints import inject_env_endpoints


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Remove any pre-existing LLM_PROXY_ENDPOINT_* vars so each test starts clean."""
    for key in list(os.environ):
        if key.startswith("LLM_PROXY_ENDPOINT_"):
            monkeypatch.delenv(key, raising=False)


def test_no_env_vars_returns_empty():
    cfg = {"endpoints": {}}
    assert inject_env_endpoints(cfg) == []
    assert cfg["endpoints"] == {}


def test_minimal_url_only_registers_noauth_endpoint(monkeypatch):
    monkeypatch.setenv("LLM_PROXY_ENDPOINT_LOCAL_URL", "http://192.168.1.50:1234/v1")
    cfg = {"endpoints": {}}
    injected = inject_env_endpoints(cfg)

    assert injected == ["local"]
    ep = cfg["endpoints"]["local"]
    assert ep["base_url"] == "http://192.168.1.50:1234/v1"
    assert ep["provider"] == "openai-compatible"
    assert ep["auth_type"] == "none"
    assert ep["_source"] == "env"
    assert ep["models"] == []
    assert "api_key_env" not in ep


def test_key_triggers_bearer_auth_with_indirect_env(monkeypatch):
    monkeypatch.setenv("LLM_PROXY_ENDPOINT_VLLM_URL", "https://x.example/v1")
    monkeypatch.setenv("LLM_PROXY_ENDPOINT_VLLM_KEY", "sk-secret")
    cfg = {"endpoints": {}}
    inject_env_endpoints(cfg)

    ep = cfg["endpoints"]["vllm"]
    assert ep["auth_type"] == "bearer"
    assert ep["api_key_env"] == "LLM_PROXY_ENDPOINT_VLLM_KEY"


def test_models_csv_is_parsed_and_trimmed(monkeypatch):
    monkeypatch.setenv("LLM_PROXY_ENDPOINT_LMSTUDIO_URL", "http://127.0.0.1:1234/v1")
    monkeypatch.setenv(
        "LLM_PROXY_ENDPOINT_LMSTUDIO_MODELS",
        " llama-3.3-70b , qwen-2.5-coder-32b , ,  ",
    )
    cfg = {"endpoints": {}}
    inject_env_endpoints(cfg)

    assert cfg["endpoints"]["lmstudio"]["models"] == [
        "llama-3.3-70b",
        "qwen-2.5-coder-32b",
    ]


def test_explicit_provider_override(monkeypatch):
    monkeypatch.setenv("LLM_PROXY_ENDPOINT_CUSTOM_URL", "http://10.0.0.1:8000/v1")
    monkeypatch.setenv("LLM_PROXY_ENDPOINT_CUSTOM_PROVIDER", "ollama")
    cfg = {"endpoints": {}}
    inject_env_endpoints(cfg)
    assert cfg["endpoints"]["custom"]["provider"] == "ollama"


def test_config_yaml_wins_on_id_collision(monkeypatch):
    monkeypatch.setenv("LLM_PROXY_ENDPOINT_LOCAL_URL", "http://192.168.99.99/v1")
    cfg = {
        "endpoints": {
            "local": {"provider": "existing", "base_url": "http://prev/v1"},
        }
    }
    injected = inject_env_endpoints(cfg)
    assert injected == []
    assert cfg["endpoints"]["local"]["provider"] == "existing"


def test_empty_url_is_ignored(monkeypatch):
    """Entries that have only a KEY / MODELS but no URL should be skipped."""
    monkeypatch.setenv("LLM_PROXY_ENDPOINT_ORPHAN_KEY", "sk-something")
    monkeypatch.setenv("LLM_PROXY_ENDPOINT_ORPHAN_MODELS", "a,b")
    cfg = {"endpoints": {}}
    assert inject_env_endpoints(cfg) == []


def test_multiple_endpoints_registered(monkeypatch):
    monkeypatch.setenv("LLM_PROXY_ENDPOINT_A_URL", "http://a.lan/v1")
    monkeypatch.setenv("LLM_PROXY_ENDPOINT_B_URL", "http://b.lan/v1")
    monkeypatch.setenv("LLM_PROXY_ENDPOINT_B_KEY", "sk-b")
    cfg = {"endpoints": {}}
    injected = inject_env_endpoints(cfg)
    assert sorted(injected) == ["a", "b"]
    assert cfg["endpoints"]["a"]["auth_type"] == "none"
    assert cfg["endpoints"]["b"]["auth_type"] == "bearer"


def test_idempotent_second_call_is_noop(monkeypatch):
    """Running inject twice on the same config adds nothing the second time."""
    monkeypatch.setenv("LLM_PROXY_ENDPOINT_FOO_URL", "http://foo/v1")
    cfg = {"endpoints": {}}
    first = inject_env_endpoints(cfg)
    second = inject_env_endpoints(cfg)
    assert first == ["foo"]
    assert second == []
    assert len(cfg["endpoints"]) == 1


def test_setdefault_creates_endpoints_key_when_missing(monkeypatch):
    monkeypatch.setenv("LLM_PROXY_ENDPOINT_X_URL", "http://x/v1")
    cfg: dict = {}
    inject_env_endpoints(cfg)
    assert "endpoints" in cfg
    assert "x" in cfg["endpoints"]

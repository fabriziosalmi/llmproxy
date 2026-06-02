"""Unit tests for core/ready_banner.py — boot-time ready banner."""

import io
import os

import pytest

from core import ready_banner


@pytest.fixture(autouse=True)
def _disable_color(monkeypatch):
    # Keep the banner output stable across environments by forcing plain text.
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    # Clean any provider keys so masking logic and "active providers" stay
    # deterministic regardless of the developer's real shell state.
    for k in list(os.environ):
        if k.endswith("_API_KEY") or k == "LLM_PROXY_API_KEYS":
            monkeypatch.delenv(k, raising=False)


def _render(cfg, **kwargs):
    buf = io.StringIO()
    import sys

    orig = sys.stdout
    sys.stdout = buf
    try:
        ready_banner.print_ready_banner(cfg, **kwargs)
    finally:
        sys.stdout = orig
    return buf.getvalue()


# ── _mask_key ──────────────────────────────────────────────────────────────


def test_mask_key_not_set():
    assert ready_banner._mask_key("") == "(not set)"


def test_mask_key_short():
    assert ready_banner._mask_key("short") == "shor…"


def test_mask_key_normal_length():
    masked = ready_banner._mask_key("sk-proxy-abcdef1234567890")
    assert masked.startswith("sk-proxy-a")
    assert masked.endswith("7890")
    assert "…" in masked


# ── _active_providers ──────────────────────────────────────────────────────


def test_active_providers_no_auth_always_active():
    cfg = {
        "endpoints": {
            "ollama": {"provider": "ollama", "models": ["llama3"], "auth_type": "none"},
        }
    }
    active = ready_banner._active_providers(cfg)
    assert len(active) == 1
    assert active[0][0] == "ollama"


def test_active_providers_key_present_is_active(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-proj-realkey123456789")
    cfg = {
        "endpoints": {
            "openai": {
                "provider": "openai",
                "models": ["gpt-4o"],
                "api_key_env": "OPENAI_API_KEY",
            },
        }
    }
    assert len(ready_banner._active_providers(cfg)) == 1


def test_active_providers_placeholder_key_filtered(monkeypatch):
    # The '...' sentinel in .env.example must not count as a usable key.
    monkeypatch.setenv("OPENAI_API_KEY", "sk-proj-...")
    cfg = {
        "endpoints": {
            "openai": {
                "provider": "openai",
                "models": ["gpt-4o"],
                "api_key_env": "OPENAI_API_KEY",
            },
        }
    }
    assert ready_banner._active_providers(cfg) == []


def test_active_providers_missing_key_filtered():
    # No env var set → endpoint shouldn't appear in the active list.
    cfg = {
        "endpoints": {
            "foo": {
                "provider": "foo",
                "models": ["m"],
                "api_key_env": "NEVER_SET_FOO_KEY_XYZ",
            },
        }
    }
    assert ready_banner._active_providers(cfg) == []


# ── print_ready_banner ─────────────────────────────────────────────────────


def test_banner_onboarding_mode_mentions_no_providers():
    cfg = {"server": {}, "endpoints": {}}
    out = _render(
        cfg,
        bind_host="0.0.0.0",
        bind_port=8090,
        firewall_enabled=True,
        firewall_reason=None,
    )
    assert "Onboarding mode" in out
    assert "no active providers" in out
    # Onboarding hint should surface all three escape hatches.
    assert "/ui" in out
    assert "OPENAI_API_KEY" in out
    assert "Ollama" in out


def test_banner_lists_active_providers():
    cfg = {
        "server": {},
        "endpoints": {
            "ollama": {
                "provider": "ollama",
                "models": ["llama3.3", "qwen3"],
                "auth_type": "none",
                "_source": "auto-discovery",
            },
        },
    }
    out = _render(
        cfg,
        bind_host="127.0.0.1",
        bind_port=8090,
        firewall_enabled=True,
        firewall_reason=None,
    )
    assert "Active providers (1)" in out
    assert "[auto-discovery]" in out
    assert "llama3.3" in out


def test_banner_waf_off_shows_reason():
    cfg = {"server": {}, "endpoints": {}}
    out = _render(
        cfg,
        bind_host="0.0.0.0",
        bind_port=8090,
        firewall_enabled=False,
        firewall_reason="env:LLM_PROXY_FIREWALL_ENABLED",
    )
    assert "OFF" in out
    assert "env:LLM_PROXY_FIREWALL_ENABLED" in out


def test_banner_auth_disabled_warning():
    cfg = {"server": {"auth": {"enabled": False}}, "endpoints": {}}
    out = _render(
        cfg,
        bind_host="0.0.0.0",
        bind_port=8090,
        firewall_enabled=True,
        firewall_reason=None,
    )
    assert "disabled" in out
    assert "development mode" in out


def test_banner_auth_required_masks_key(monkeypatch):
    monkeypatch.setenv("LLM_PROXY_API_KEYS", "sk-proxy-abcdef1234567890,other")
    cfg = {
        "server": {"auth": {"enabled": True, "api_keys_env": "LLM_PROXY_API_KEYS"}},
        "endpoints": {},
    }
    out = _render(
        cfg,
        bind_host="0.0.0.0",
        bind_port=8090,
        firewall_enabled=True,
        firewall_reason=None,
    )
    # Ensure we never print the full key in cleartext.
    assert "sk-proxy-abcdef1234567890" not in out
    # The masked form must appear with the prefix + suffix.
    assert "sk-proxy-a" in out
    assert "7890" in out


def test_banner_smoke_curl_uses_first_model():
    cfg = {
        "server": {},
        "endpoints": {
            "ollama": {
                "provider": "ollama",
                "models": ["llama3.3", "qwen3"],
                "auth_type": "none",
            },
        },
    }
    out = _render(
        cfg,
        bind_host="127.0.0.1",
        bind_port=8090,
        firewall_enabled=True,
        firewall_reason=None,
    )
    assert '"model":"llama3.3"' in out


def test_banner_loopback_display_for_any_bind():
    """bind_host='0.0.0.0' must render as 'localhost' in the URL shown to humans."""
    cfg = {"server": {}, "endpoints": {}}
    out = _render(
        cfg,
        bind_host="0.0.0.0",
        bind_port=8090,
        firewall_enabled=True,
        firewall_reason=None,
    )
    assert "http://localhost:8090/v1" in out
    assert "http://0.0.0.0:8090" not in out

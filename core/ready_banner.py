"""
Boot-time ready banner.

Prints a compact, copy-paste-friendly summary of the running proxy once
setup completes: listening URL, active providers + sample models, WAF
state, onboarding mode if no endpoints are ready, and a curl template
for the first request. Anti-ansiety layer for the 60-second test.

The banner goes to stdout (not the structured logger) so it stays readable
in `docker compose logs` without being re-wrapped or prefixed with
ISO timestamps.
"""

from __future__ import annotations

import os
import sys
from typing import Any


_RESET = "\033[0m"
_BOLD = "\033[1m"
_DIM = "\033[2m"
_CYAN = "\033[38;5;51m"
_GREEN = "\033[38;5;46m"
_YELLOW = "\033[38;5;226m"
_RED = "\033[38;5;196m"
_GRAY = "\033[38;5;242m"


def _colorize() -> bool:
    # Disable ANSI when piping to a file or when NO_COLOR is set.
    if os.environ.get("NO_COLOR"):
        return False
    return sys.stdout.isatty() or os.environ.get("FORCE_COLOR") == "1"


def _wrap(text: str, code: str) -> str:
    if not _colorize():
        return text
    return f"{code}{text}{_RESET}"


def _mask_key(key: str) -> str:
    """Mask an auth key for display. Keeps enough bytes to identify it
    without exposing it to log aggregators."""
    if not key:
        return "(not set)"
    if len(key) <= 14:
        return key[:4] + "…"
    return f"{key[:10]}…{key[-4:]}"


def _active_providers(config: dict[str, Any]) -> list[tuple[str, str, list[str], str]]:
    """Return [(endpoint_id, provider, models, source)] for endpoints with a
    usable credential. Auto-discovered and no-auth endpoints count as
    active."""
    out = []
    for ep_id, ep in (config.get("endpoints") or {}).items():
        provider = ep.get("provider", ep_id)
        auth_type = ep.get("auth_type", "bearer")
        models = ep.get("models") or []
        source = ep.get("_source", "config")

        if auth_type == "none":
            out.append((ep_id, provider, models, source))
            continue

        key_env = ep.get("api_key_env")
        if not key_env:
            continue
        val = os.environ.get(key_env, "")
        _placeholders = {
            "sk-proj-...",
            "sk-ant-...",
            "AIza...",
            "gsk_...",
            "your-api-key",
            "CHANGE-ME",
            "",
        }
        if val and val not in _placeholders:
            out.append((ep_id, provider, models, source))
    return out


def _get_key_count(key_env: str) -> int:
    """Return the number of configured keys without loading/exposing the keys."""
    raw = os.environ.get(key_env, "")
    return len(raw.split(",")) if raw else 0


def print_ready_banner(
    config: dict[str, Any],
    *,
    bind_host: str,
    bind_port: int,
    firewall_enabled: bool,
    firewall_reason: str | None,
) -> None:
    # The auth env var lookup honours the configured name so custom deployments
    # still see the correct key location.
    auth_cfg = config.get("server", {}).get("auth", {})
    auth_enabled = auth_cfg.get("enabled", False)
    key_env = auth_cfg.get("api_keys_env", "LLM_PROXY_API_KEYS")

    # Pick a user-reachable display host. "0.0.0.0" in the proxy means
    # "bind all interfaces" — the user still types localhost in curl.
    display_host = "localhost" if bind_host in ("0.0.0.0", "::", "") else bind_host
    api_base = f"http://{display_host}:{bind_port}"

    providers = _active_providers(config)

    # Opening rule + title
    out: list[str] = []
    out.append("")
    out.append(_wrap("━" * 70, _CYAN))
    out.append(_wrap("  LLMProxy is ready", _BOLD) + _wrap(f"   {api_base}/v1", _CYAN))
    out.append(_wrap("━" * 70, _CYAN))

    # Providers table
    if not providers:
        out.append("")
        out.append(_wrap("  ! Onboarding mode — no active providers yet", _YELLOW))
        out.append(_wrap("    Inference requests will return 503.", _GRAY))
        out.append(_wrap("    Fix by doing ONE of the following:", _GRAY))
        out.append(_wrap("      • Open " + api_base + "/ui and add an endpoint", _GRAY))
        out.append(
            _wrap("      • Set a provider key in .env (OPENAI_API_KEY=…)", _GRAY)
        )
        out.append(
            _wrap(
                "      • Start Ollama or LM Studio locally — auto-detected on restart",
                _GRAY,
            )
        )
    else:
        out.append("")
        out.append(_wrap(f"  Active providers ({len(providers)}):", _BOLD))
        for ep_id, provider, models, source in providers:
            sample = ", ".join(models[:3])
            if len(models) > 3:
                sample += f", +{len(models) - 3} more"
            tag_raw = f"[{source}]"
            tag_color = _GREEN if source == "auto-discovery" else _GRAY
            tag = _wrap(tag_raw.ljust(17), tag_color)
            name = _wrap(f"{ep_id:<14}", _CYAN)
            prov = _wrap(f"({provider})", _DIM)
            out.append(f"    {tag} {name} {prov}")
            if sample:
                out.append(f"      {_wrap(sample, _GRAY)}")

    # Security state
    out.append("")
    if firewall_enabled:
        out.append(
            _wrap("  WAF:    ", _BOLD)
            + _wrap("ON", _GREEN)
            + _wrap("   (byte-level ASGI injection firewall)", _GRAY)
        )
    else:
        out.append(
            _wrap("  WAF:    ", _BOLD)
            + _wrap("OFF", _RED)
            + _wrap(f"  ({firewall_reason})", _GRAY)
        )
    if auth_enabled:
        key_count = _get_key_count(key_env)
        out.append(
            _wrap("  Auth:   ", _BOLD)
            + _wrap("required", _GREEN)
            + _wrap(f"   ({key_count} Bearer key(s) configured in ${key_env})", _GRAY)
        )
    else:
        out.append(
            _wrap("  Auth:   ", _BOLD)
            + _wrap("disabled", _YELLOW)
            + _wrap("  (development mode — anyone can call)", _GRAY)
        )

    # Copy-paste curl
    out.append("")
    out.append(_wrap("  Smoke test:", _BOLD))
    # Use the first configured model when available so the example is
    # actually runnable without edits.
    default_model = "gpt-4o-mini"
    for _ep_id, _prov, models, _src in providers:
        if models:
            default_model = models[0]
            break
    if auth_enabled:
        key_expr = f"$(grep -oP '^{key_env}=\\K[^,]+' .env | head -1)"
        auth_hdr = f"-H 'Authorization: Bearer {key_expr}' "
    else:
        auth_hdr = ""
    curl = (
        f"    curl {api_base}/v1/chat/completions \\\n"
        f"      {auth_hdr}-H 'Content-Type: application/json' \\\n"
        f'      -d \'{{"model":"{default_model}","messages":[{{"role":"user","content":"hi"}}]}}\''
    )
    out.append(_wrap(curl, _GRAY))
    out.append("")
    out.append(_wrap("  Admin UI: ", _BOLD) + _wrap(f"{api_base}/ui", _CYAN))
    out.append(_wrap("━" * 70, _CYAN))
    out.append("")

    sys.stdout.write("\n".join(out) + "\n")
    sys.stdout.flush()

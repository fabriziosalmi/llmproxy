"""LLMProxy — Config loader.

Loads `config.yaml`, applies env-based overlays, and computes a content
hash for the hot-reload watcher. Pure functions — no instance state. The
orchestrator wraps them so it can pass `self.config_path` once.

Extracted from proxy/rotator.py to keep the orchestrator focused on
wiring + request dispatch.
"""

from __future__ import annotations

import hashlib
import os
import logging
from typing import Any, Dict

import yaml

logger = logging.getLogger("llmproxy.config_loader")


def dev_mode_enabled() -> bool:
    """True when the operator asked to run open, via LLM_PROXY_DEV_MODE."""
    return os.environ.get("LLM_PROXY_DEV_MODE", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def apply_dev_mode(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Turn authentication off when LLM_PROXY_DEV_MODE is set, loudly.

    This used to apply only when config.yaml was ABSENT, which made it useless
    in the case it exists for: the published image always has a config file, so
    the only way to run open was to edit the shipped YAML — and the shipped YAML
    did it for you. Now the file says `enabled: true` and this is the documented
    way to opt out, so running open is a decision someone made rather than a
    default they inherited.

    Applied after the YAML parse so the env wins, mirroring the firewall
    override below. Mutates and returns `cfg`.
    """
    if not dev_mode_enabled():
        return cfg
    cfg.setdefault("server", {}).setdefault("auth", {})["enabled"] = False
    logger.warning(
        "LLM_PROXY_DEV_MODE=1 — authentication is DISABLED. Every route, "
        "including the control plane and /api/v1/config/raw, answers without a "
        "credential. Do not use this outside local development.",
    )
    return cfg


def load_config(config_path: str) -> Dict[str, Any]:
    """Load YAML config + apply env overlays.

    Missing file → fail-closed default (auth enabled). Set
    `LLM_PROXY_DEV_MODE=1` to intentionally run open in local development.
    Env overlays are reapplied on every call so hot-reload picks up new
    env values without needing a YAML edit.
    """
    if os.path.exists(config_path):
        with open(config_path, "r") as f:
            cfg = yaml.safe_load(f) or {}
    else:
        cfg = {"server": {"auth": {"enabled": True}}}
        logger.warning(
            "Config file '%s' not found — fail-closed defaults applied (auth enabled).",
            config_path,
        )

    apply_dev_mode(cfg)

    # Env-based endpoint overlay — runs on every config reload (boot + hot
    # reload watcher). Keeps LLM_PROXY_ENDPOINT_<NAME>_* declarations in
    # sync with the live config without requiring YAML edits.
    from core.env_endpoints import inject_env_endpoints

    inject_env_endpoints(cfg)

    # Env override for the WAF toggle. Applied after YAML so env wins.
    firewall_env = os.environ.get("LLM_PROXY_FIREWALL_ENABLED")
    if firewall_env is not None:
        enabled = firewall_env.strip().lower() not in ("0", "false", "off", "no", "")
        cfg.setdefault("security", {}).setdefault("firewall", {})["enabled"] = enabled

    return cfg


def compute_config_hash(config_path: str) -> str:
    """Blocking MD5 of the config file. Run via to_thread() from async.

    Empty string when the file doesn't exist (callers compare for change
    detection — a missing-then-missing transition is correctly a no-op).
    """
    if os.path.exists(config_path):
        with open(config_path, "rb") as f:
            return hashlib.md5(f.read(), usedforsecurity=False).hexdigest()
    return ""

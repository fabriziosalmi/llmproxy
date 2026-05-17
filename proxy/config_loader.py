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


def load_config(config_path: str) -> Dict[str, Any]:
    """Load YAML config + apply env overlays.

    Missing file → fail-closed default (auth enabled). Set
    `LLM_PROXY_DEV_MODE=1` to intentionally run open in local development.
    Env overlays are reapplied on every call so hot-reload picks up new
    env values without needing a YAML edit.
    """
    if os.path.exists(config_path):
        with open(config_path, 'r') as f:
            cfg = yaml.safe_load(f) or {}
    else:
        dev_mode = os.environ.get("LLM_PROXY_DEV_MODE", "").strip().lower() in ("1", "true", "yes", "on")
        cfg = {"server": {"auth": {"enabled": not dev_mode}}}
        if dev_mode:
            logger.warning(
                "Config file '%s' not found — running in DEV MODE with auth disabled "
                "(LLM_PROXY_DEV_MODE=1). Do not use in production.",
                config_path,
            )
        else:
            logger.warning(
                "Config file '%s' not found — fail-closed defaults applied (auth enabled).",
                config_path,
            )

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
        with open(config_path, 'rb') as f:
            return hashlib.md5(f.read(), usedforsecurity=False).hexdigest()
    return ""

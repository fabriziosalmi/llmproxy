"""
Env-based endpoint bootstrap.

Allows defining OpenAI-compatible endpoints (LM Studio, vLLM, TGI, Ollama,
remote OpenAI-compatible APIs, private gateways...) purely via environment
variables, without editing config.yaml.

Naming convention:
    LLM_PROXY_ENDPOINT_<NAME>_URL     — base URL (required)
    LLM_PROXY_ENDPOINT_<NAME>_KEY     — bearer token (optional; empty => no-auth)
    LLM_PROXY_ENDPOINT_<NAME>_MODELS  — comma-separated model IDs (optional)
    LLM_PROXY_ENDPOINT_<NAME>_PROVIDER — adapter hint (default: "openai-compatible")

<NAME> is case-insensitive and becomes the lowercased endpoint id.

Example — LM Studio on the LAN:
    LLM_PROXY_ENDPOINT_LOCAL_URL=http://192.168.1.50:1234/v1
    LLM_PROXY_ENDPOINT_LOCAL_MODELS=llama-3.3-70b,qwen-2.5-coder-32b
"""

from __future__ import annotations

import os
import logging
from typing import Any

logger = logging.getLogger("llmproxy.env_endpoints")

_PREFIX = "LLM_PROXY_ENDPOINT_"
_SUFFIX_URL = "_URL"
_VALID_SUFFIXES = {"_URL", "_KEY", "_MODELS", "_PROVIDER"}


def _iter_endpoint_names() -> list[str]:
    """Return distinct endpoint names parsed from matching env vars."""
    names: set[str] = set()
    for key in os.environ:
        if not key.startswith(_PREFIX):
            continue
        tail = key[len(_PREFIX):]
        for suffix in _VALID_SUFFIXES:
            if tail.endswith(suffix):
                name = tail[:-len(suffix)]
                if name:
                    names.add(name)
                break
    return sorted(names)


def inject_env_endpoints(config: dict[str, Any]) -> list[str]:
    """Merge env-declared endpoints into config['endpoints'].

    Returns the list of injected endpoint ids for logging.
    Endpoints already present in config.yaml win on id collision.
    """
    endpoints = config.setdefault("endpoints", {})
    injected: list[str] = []

    for raw_name in _iter_endpoint_names():
        url_env = f"{_PREFIX}{raw_name}{_SUFFIX_URL}"
        url = os.environ.get(url_env, "").strip()
        if not url:
            continue

        ep_id = raw_name.lower()
        if ep_id in endpoints:
            logger.warning(
                "Env endpoint '%s' overridden by config.yaml (same id) — skipping env entry",
                ep_id,
            )
            continue

        key_env = f"{_PREFIX}{raw_name}_KEY"
        # We reference the env var name (not the value) so SecretManager /
        # Infisical hooks work consistently. If no key var is set the adapter
        # runs with auth_type=none (suitable for local Ollama / LM Studio).
        has_key = bool(os.environ.get(key_env, "").strip())

        models_raw = os.environ.get(f"{_PREFIX}{raw_name}_MODELS", "").strip()
        models = [m.strip() for m in models_raw.split(",") if m.strip()] if models_raw else []

        provider = os.environ.get(f"{_PREFIX}{raw_name}_PROVIDER", "").strip() or "openai-compatible"

        endpoints[ep_id] = {
            "provider": provider,
            "base_url": url,
            "models": models,
            # Record where this endpoint came from so the UI can surface it.
            "_source": "env",
            **({"api_key_env": key_env, "auth_type": "bearer"}
               if has_key else {"auth_type": "none"}),
        }
        injected.append(ep_id)
        logger.info(
            "Registered env endpoint '%s' -> %s (provider=%s, models=%d, auth=%s)",
            ep_id, url, provider, len(models), "bearer" if has_key else "none",
        )

    return injected

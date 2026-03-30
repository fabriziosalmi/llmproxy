"""
Model resolver — aliases and groups for decoupling clients from providers.

Resolves user-facing model names to real provider models before routing:
  - Aliases: "gpt4" → "gpt-4o", "fast" → "gpt-4o-mini"
  - Groups: "auto" → cheapest/fastest/random from pool of AVAILABLE models
  - Pass-through: unknown models forwarded as-is

Resilient routing: groups filter out models whose provider has no valid
API key configured. If "auto" has [openai, google, anthropic] but only
google has a key, it resolves to a Google model.
"""

import os
import random
import logging
from typing import Dict, Any, Optional, List

logger = logging.getLogger("llmproxy.model_resolver")

# Cache of available providers (set by _check_available_providers)
_available_providers: set[str] | None = None


def _get_available_providers(config: Dict[str, Any]) -> set[str]:
    """Return set of provider names that have valid API keys configured."""
    global _available_providers
    if _available_providers is not None:
        return _available_providers

    _PLACEHOLDERS = {"sk-proj-...", "sk-ant-...", "AIza...", "gsk_...", "your-api-key", "CHANGE-ME", ""}
    available = set()
    endpoints = config.get("endpoints", {})
    for name, ep_cfg in endpoints.items():
        api_key_env = ep_cfg.get("api_key_env", "")
        auth_type = ep_cfg.get("auth_type", "bearer")
        if auth_type == "none":
            available.add(name)
            continue
        if api_key_env:
            val = os.environ.get(api_key_env, "")
            if val and val not in _PLACEHOLDERS:
                available.add(name)
    _available_providers = available
    return available


def resolve_model(config: Dict[str, Any], requested_model: str) -> tuple[str, str | None]:
    """Resolve a model alias or group to a real model name + provider.

    Returns (model_name, provider_name_or_None).
    When resolving from a group, the provider is returned so the router
    knows which endpoint to use (prevents model/endpoint mismatch).
    """
    # 1. Check aliases
    aliases = config.get("model_aliases", {})
    if requested_model in aliases:
        resolved = str(aliases[requested_model])
        logger.debug(f"Alias resolved: {requested_model} → {resolved}")
        return resolved, None

    # 2. Check groups
    groups = config.get("model_groups", {})
    if requested_model in groups:
        group = groups[requested_model]
        available = _get_available_providers(config)
        result = _pick_from_group(group, available)
        if result:
            model, provider = result
            logger.debug(f"Group resolved: {requested_model} → {model} ({provider})")
            return model, provider

    # 3. Pass-through
    return requested_model, None


def invalidate_provider_cache():
    """Called on config reload to re-check available providers."""
    global _available_providers
    _available_providers = None


def _filter_available(models: List[Dict], available: set[str]) -> List[Dict]:
    """Filter models to only those whose provider has a valid API key."""
    if not available:
        return models  # No info — return all (best effort)
    filtered = [m for m in models if m.get("provider", "") in available]
    if not filtered:
        logger.warning(f"No available providers for group models. Available: {available}")
        return models  # Fallback to all — let the forwarder handle errors
    return filtered


def _pick_from_group(group: Dict[str, Any], available: set[str]) -> Optional[tuple[str, str]]:
    """Pick a model from a group based on strategy.

    Returns (model_name, provider_name) or None.
    Only considers models whose provider is available (has API key).
    """
    models = _filter_available(group.get("models", []), available)
    if not models:
        return None

    strategy = group.get("strategy", "random")

    if strategy == "cheapest":
        from core.pricing import get_pricing
        chosen = min(models, key=lambda m: get_pricing(m["model"])["input"])
    elif strategy == "fastest":
        try:
            from plugins.default.neural_router import get_endpoint_stats
        except ImportError:
            logger.debug("neural_router not available for 'fastest' strategy, falling back to random")
            chosen = random.choice(models)
            return str(chosen["model"]), str(chosen.get("provider", ""))

        def _latency(m):
            stats = get_endpoint_stats(m.get("provider", ""))
            return stats.get("latency_ms", 999.0)
        chosen = min(models, key=_latency)
    elif strategy == "weighted":
        weights = [m.get("weight", 1.0) for m in models]
        chosen = random.choices(models, weights=weights, k=1)[0]
    else:  # "random"
        chosen = random.choice(models)

    return str(chosen["model"]), str(chosen.get("provider", ""))

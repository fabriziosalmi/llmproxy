"""
Model resolver — aliases and groups for decoupling clients from providers.

Resolves user-facing model names to real provider models before routing:
  - Aliases: "gpt4" → "gpt-4o", "fast" → "gpt-4o-mini"
  - Groups: "auto" → cheapest/fastest/random from pool of models
  - Pass-through: unknown models forwarded as-is

Config (config.yaml):
  model_aliases:
    "gpt4": "gpt-4o"
    "claude": "claude-sonnet-4-20250514"
    "fast": "gpt-4o-mini"

  model_groups:
    "auto":
      strategy: "cheapest"
      models:
        - { model: "gpt-4o-mini", provider: "openai", weight: 0.5 }
        - { model: "gemini-2.0-flash", provider: "google", weight: 0.3 }
"""

import random
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger("llmproxy.model_resolver")


def resolve_model(config: Dict[str, Any], requested_model: str) -> str:
    """Resolve a model alias or group to a real model name.

    Returns the resolved model name (or the original if no match).
    """
    # 1. Check aliases
    aliases = config.get("model_aliases", {})
    if requested_model in aliases:
        resolved = aliases[requested_model]
        logger.debug(f"Alias resolved: {requested_model} → {resolved}")
        return resolved

    # 2. Check groups
    groups = config.get("model_groups", {})
    if requested_model in groups:
        group = groups[requested_model]
        resolved = _pick_from_group(group)
        if resolved:
            logger.debug(f"Group resolved: {requested_model} → {resolved}")
            return resolved

    # 3. Pass-through
    return requested_model


def _pick_from_group(group: Dict[str, Any]) -> Optional[str]:
    """Pick a model from a group based on strategy."""
    models = group.get("models", [])
    if not models:
        return None

    strategy = group.get("strategy", "random")

    if strategy == "cheapest":
        from core.pricing import get_pricing
        return min(models, key=lambda m: get_pricing(m["model"])["input"])["model"]

    elif strategy == "fastest":
        from plugins.default.neural_router import get_endpoint_stats
        # Pick model with lowest known latency (or first if no data)
        def _latency(m):
            stats = get_endpoint_stats(m.get("provider", ""))
            return stats.get("latency_ms", 999.0)
        return min(models, key=_latency)["model"]

    elif strategy == "weighted":
        weights = [m.get("weight", 1.0) for m in models]
        chosen = random.choices(models, weights=weights, k=1)[0]
        return chosen["model"]

    else:  # "random"
        return random.choice(models)["model"]

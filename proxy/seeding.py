"""LLMProxy — Endpoint seeding.

Registers endpoints declared in `config.yaml` into the persistence store
on boot (and on demand from the registry routes after re-discovery).
Skips entries with placeholder API keys so a half-configured `.env`
doesn't pollute the live registry with un-callable endpoints.

Extracted from proxy/rotator.py.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict

from models import LLMEndpoint, EndpointStatus

logger = logging.getLogger("llmproxy.seeding")


# API-key placeholders shipped in the bundled `config.yaml`. An entry whose
# api_key_env resolves to one of these means the operator hasn't filled it
# in yet — skip seeding so the UI registry isn't littered with endpoints
# that 401 on first call.
_KEY_PLACEHOLDERS = frozenset({
    "sk-proj-...",
    "sk-ant-...",
    "AIza...",
    "gsk_...",
    "your-api-key",
    "CHANGE-ME",
    "",
})


async def seed_endpoints_from_config(config: Dict[str, Any], store: Any) -> int:
    """Persist config.yaml endpoints into `store` if not already present.

    Returns the number of new endpoints seeded.

    Skips entries that already exist (matched by id), have no `base_url`,
    or whose api_key_env value is a known placeholder.
    """
    endpoints_cfg = config.get("endpoints", {})
    existing = await store.get_all()
    existing_ids = {ep.id for ep in existing}

    seeded = 0
    for name, ep_cfg in endpoints_cfg.items():
        if name in existing_ids:
            continue

        provider = ep_cfg.get("provider", name)
        base_url = ep_cfg.get("base_url", "")
        api_key_env = ep_cfg.get("api_key_env", "")
        models = ep_cfg.get("models", [])

        if not base_url:
            continue

        if api_key_env:
            key_val = os.environ.get(api_key_env, "")
            if not key_val or key_val in _KEY_PLACEHOLDERS:
                continue

        ep = LLMEndpoint(
            id=name,
            url=base_url,
            status=EndpointStatus.VERIFIED,
            metadata={
                "provider": provider,
                "models": models,
                "api_key_env": api_key_env,
            },
        )
        await store.add_endpoint(ep)
        logger.info(f"Seeded endpoint '{name}' ({provider}) with {len(models)} models")
        seeded += 1
    return seeded

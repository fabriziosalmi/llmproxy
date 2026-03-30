"""GET /v1/models — OpenAI-compatible model discovery endpoint.

Every OpenAI-compatible client (Cursor, Continue.dev, OpenWebUI, LibreChat,
TypingMind, Jan, LM Studio) calls this before anything else. Without it,
90% of GUI clients won't work without manual configuration.

Aggregates models from all endpoints configured in config.yaml.
"""

import time
import logging
from fastapi import APIRouter

logger = logging.getLogger("llmproxy.routes.models")


def create_router(agent) -> APIRouter:
    router = APIRouter()

    @router.get("/v1/models")
    async def list_models():
        """Return all available models in OpenAI list format."""
        endpoints_cfg = agent.config.get("endpoints", {})
        created = int(time.time())
        models = []
        seen = set()

        for ep_name, ep_config in endpoints_cfg.items():
            provider = ep_config.get("provider", ep_name)
            ep_models = ep_config.get("models", [])

            for model_id in ep_models:
                if model_id in seen:
                    continue
                seen.add(model_id)
                models.append({
                    "id": model_id,
                    "object": "model",
                    "created": created,
                    "owned_by": provider,
                })

        # Sort alphabetically by provider then model id for stable output
        models.sort(key=lambda m: (m["owned_by"], m["id"]))

        return {"object": "list", "data": models}

    @router.get("/v1/models/{model_id:path}")
    async def get_model(model_id: str):
        """Return a single model's info (required by some clients)."""
        endpoints_cfg = agent.config.get("endpoints", {})

        for ep_name, ep_config in endpoints_cfg.items():
            provider = ep_config.get("provider", ep_name)
            if model_id in ep_config.get("models", []):
                return {
                    "id": model_id,
                    "object": "model",
                    "created": int(time.time()),
                    "owned_by": provider,
                }

        # Model not in config — still return it (proxy can forward unknown models)
        from proxy.adapters.registry import detect_provider
        return {
            "id": model_id,
            "object": "model",
            "created": int(time.time()),
            "owned_by": detect_provider(model_id),
        }

    return router

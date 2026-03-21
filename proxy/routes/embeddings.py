"""POST /v1/embeddings — OpenAI-compatible embedding endpoint.

Critical for RAG pipelines (LangChain, LlamaIndex, Haystack). Runs through
the security pipeline first — PII in document chunks is a real threat.

Supports: OpenAI, Azure, Google Gemini, Ollama, and OpenAI-compatible providers.
Anthropic has no embeddings API — requests for Anthropic models return 400.
"""

import json
import time
import logging

from fastapi import APIRouter, Request, HTTPException, Depends
from fastapi.security import APIKeyHeader

from proxy.adapters.registry import get_adapter, detect_provider

logger = logging.getLogger("llmproxy.routes.embeddings")

API_KEY_HEADER = APIKeyHeader(name="Authorization", auto_error=False)

# Embedding model → provider mapping (supplements the chat model detection)
EMBEDDING_MODEL_PROVIDERS = {
    "text-embedding-3-small": "openai",
    "text-embedding-3-large": "openai",
    "text-embedding-ada-002": "openai",
    "text-embedding-004": "google",
    "embedding-001": "google",
    "nomic-embed-text": "ollama",
    "mxbai-embed-large": "ollama",
    "all-minilm": "ollama",
    "snowflake-arctic-embed": "ollama",
    "bge-large": "ollama",
    "mistral-embed": "mistral",
}


def _detect_embedding_provider(model: str) -> str:
    """Detect provider for embedding models."""
    if model in EMBEDDING_MODEL_PROVIDERS:
        return EMBEDDING_MODEL_PROVIDERS[model]
    # Fall back to the general chat model detection
    return detect_provider(model)


def create_router(agent) -> APIRouter:
    router = APIRouter()

    @router.post("/v1/embeddings")
    async def embeddings(request: Request, api_key: str = Depends(API_KEY_HEADER)):
        from core.metrics import MetricsTracker
        from core.pricing import estimate_cost

        # Auth — same as chat endpoint
        token = ""
        if agent.config["server"]["auth"]["enabled"]:
            if not api_key:
                raise HTTPException(status_code=401, detail="Unauthorized: Missing API key")
            token = api_key.replace("Bearer ", "").strip()
            if not token:
                raise HTTPException(status_code=401, detail="Unauthorized: Empty token")

            if not agent.identity.enabled:
                valid_keys = agent._get_api_keys()
                if token not in valid_keys:
                    MetricsTracker.track_auth_failure("invalid_key")
                    raise HTTPException(status_code=401, detail="Unauthorized: Invalid API key")

        body = await request.json()
        model = body.get("model", "text-embedding-3-small")
        text_input = body.get("input", "")

        # Security: inspect input text for PII and injection
        # Normalize input to messages format for SecurityShield
        if isinstance(text_input, list):
            inspect_text = " ".join(str(t) for t in text_input)
        else:
            inspect_text = str(text_input)

        session_id = token or (request.client.host if request.client else "anon")
        security_error = agent.security.inspect(
            {"messages": [{"role": "user", "content": inspect_text}]},
            session_id,
        )
        if security_error:
            logger.warning(f"SecurityShield blocked embedding: {security_error}")
            MetricsTracker.track_injection_blocked()
            raise HTTPException(status_code=403, detail=security_error)

        # Resolve provider
        provider = _detect_embedding_provider(model)
        adapter = get_adapter(provider, model)

        # Check embedding support
        if not adapter.supports_embeddings:
            raise HTTPException(
                status_code=400,
                detail=f"Provider '{adapter.provider_name}' does not support embeddings. "
                       f"Use an OpenAI, Google, or Ollama embedding model instead.",
            )

        # Resolve endpoint URL and provider API key from config
        import os
        endpoints_cfg = agent.config.get("endpoints", {})
        base_url = ""
        provider_api_key = ""
        for ep_name, ep_config in endpoints_cfg.items():
            if ep_config.get("provider") == provider or ep_name == provider:
                base_url = ep_config.get("base_url", "")
                # Load provider API key from environment (NOT the client's proxy key)
                api_key_env = ep_config.get("api_key_env", "")
                if api_key_env:
                    provider_api_key = os.environ.get(api_key_env, "")
                break

        if not base_url:
            raise HTTPException(
                status_code=502,
                detail=f"No endpoint configured for provider '{provider}'",
            )

        # Build auth headers with PROVIDER key (never forward client's proxy key)
        headers = {}
        if provider_api_key:
            headers["Authorization"] = f"Bearer {provider_api_key}"

        # Translate request
        target_url, translated_body, translated_headers = adapter.translate_embedding_request(
            base_url, body, headers,
        )

        # Forward request
        start = time.time()
        session = await agent._get_session()

        try:
            response = await adapter.request(target_url, translated_body, translated_headers, session)
        except Exception as e:
            logger.error(f"Embedding request failed: {e}")
            raise HTTPException(status_code=502, detail="Embedding upstream request failed")

        duration = time.time() - start
        MetricsTracker.track_request("POST", "/v1/embeddings", response.status_code, duration)

        # Translate response if needed (Google Gemini format → OpenAI)
        if response.status_code == 200 and hasattr(response, "body"):
            try:
                data = json.loads(response.body.decode())
                translated = adapter.translate_embedding_response(data)
                if translated is not data:
                    from starlette.responses import Response as StarletteResponse
                    response = StarletteResponse(
                        content=json.dumps(translated).encode("utf-8"),
                        status_code=200,
                        media_type="application/json",
                    )
            except (json.JSONDecodeError, KeyError):
                pass

        # Cost tracking
        try:
            if hasattr(response, "body"):
                usage = json.loads(response.body.decode()).get("usage", {})
                tokens = usage.get("total_tokens", 0) or usage.get("prompt_tokens", 0)
                cost_usd = estimate_cost(model, tokens, 0)
                agent.total_cost_today += cost_usd
        except Exception:
            pass

        return response

    return router

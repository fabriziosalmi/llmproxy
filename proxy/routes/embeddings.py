"""POST /v1/embeddings — OpenAI-compatible embedding endpoint.

Critical for RAG pipelines (LangChain, LlamaIndex, Haystack). Runs through
the security pipeline first — PII in document chunks is a real threat.

Supports: OpenAI, Azure, Google Gemini, Ollama, and OpenAI-compatible providers.
Anthropic has no embeddings API — requests for Anthropic models return 400.
"""

import datetime as _dt
import json
import logging
import time
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import APIKeyHeader

from core.session_id import (
    from_fingerprint as session_id_from_fingerprint,
)
from core.session_id import (
    from_token as session_id_from_token,
)
from proxy.adapters.registry import detect_provider, get_adapter
from proxy.audit_backlog import submit as submit_audit
from proxy.auth_helpers import audit_principal, authenticate_data_plane
from proxy.routes.deps import EmbeddingsAgent
from proxy.schemas import EmbeddingsRequest

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


async def _record(
    agent, request, token: str, seen: dict, started: float, status: int, refusal=None
) -> None:
    """The audit row (and, for a served request, the spend row) of an embedding call.

    This route wrote neither: an embedding request was charged to the budget
    and then left no trace, served or refused, in the audit log or in the
    per-key spend. It is where the documents of a RAG pipeline go through.
    """
    store = getattr(agent, "store", None)
    if store is None or not hasattr(store, "log_audit"):
        return
    from core.metrics import MetricsTracker

    now = int(time.time())
    key = audit_principal(request, token)
    latency_ms = round((time.time() - started) * 1000, 1)
    refused = refusal is not None and 400 <= status < 500
    reason = ""
    if refusal is not None:
        reason = (refusal if isinstance(refusal, str) else json.dumps(refusal, default=str))[:500]
        event = "request.refused" if refused else "request.failed"
    else:
        event = "embeddings"

    async def _write() -> None:
        if refusal is None and hasattr(store, "log_spend"):
            try:
                await store.log_spend(
                    ts=now,
                    date=_dt.date.today().isoformat(),
                    key_prefix=key,
                    model=seen["model"],
                    provider=seen["provider"],
                    prompt_tokens=seen["tokens"],
                    completion_tokens=0,
                    cost_usd=seen["cost_usd"],
                    latency_ms=latency_ms,
                    status=status,
                )
            except Exception as e:
                logger.warning("Embedding spend log failed: %s", e)
        try:
            await store.log_audit(
                ts=now,
                req_id=uuid.uuid4().hex[:16],
                session_id=seen["session_id"][:16],
                key_prefix=key,
                model=seen["model"],
                provider=seen["provider"],
                status=status,
                prompt_tokens=seen["tokens"],
                completion_tokens=0,
                cost_usd=seen["cost_usd"],
                latency_ms=latency_ms,
                blocked=refused,
                block_reason=reason,
                metadata=json.dumps({"event": event}, separators=(",", ":")),
            )
            MetricsTracker.track_audit_persistence("embeddings", "ok")
        except Exception as e:
            MetricsTracker.track_audit_persistence("embeddings", "fail")
            logger.warning("Embedding audit log failed: %s", e)

    await submit_audit(agent, _write(), route="embeddings")


def create_router(agent: EmbeddingsAgent) -> APIRouter:
    router = APIRouter()

    @router.post("/v1/embeddings")
    async def embeddings(
        request: Request,
        payload: EmbeddingsRequest,
        api_key: str = Depends(API_KEY_HEADER),
    ):
        from core.metrics import MetricsTracker
        from core.pricing import estimate_cost

        # This route never reaches request_pipeline, which is what enforces
        # quota_exceeded for chat, so it enforces here (402).
        token = await authenticate_data_plane(
            agent, request, api_key, enforce_quota=True
        )

        # The kill switch stops this route too (it was read by chat alone).
        if getattr(agent, "proxy_enabled", True) is False:
            raise HTTPException(
                status_code=503, detail="Proxy service is currently STOPPED."
            )

        started = time.time()
        # Filled in as the request proceeds, so the audit row written on the way
        # out says as much as was known when it stopped.
        seen = {"model": "", "provider": "", "session_id": "", "tokens": 0, "cost_usd": 0.0}
        try:
            # See proxy/schemas.py: validated at the boundary, forwarded unchanged.
            body = payload.to_body()
            model = body.get("model", "text-embedding-3-small")
            seen["model"] = str(model)
            text_input = body.get("input", "")

            # Security: inspect input text for PII and injection
            # Normalize input to messages format for SecurityShield
            if isinstance(text_input, list):
                inspect_text = " ".join(str(t) for t in text_input)
            else:
                inspect_text = str(text_input)

            if token:
                session_id = session_id_from_token(token)
            else:
                session_id = session_id_from_fingerprint(
                    request.client.host if request.client else "anon",
                    request.headers.get("user-agent", ""),
                    request.headers.get("accept-language", ""),
                )
            # ThreatLedger parity with request_pipeline: pass ip + key_prefix so
            # cross-session aggregation sees this route too.
            _client_ip = request.client.host if request.client else ""
            _key_prefix = session_id[:8] if session_id != "default" else ""
            seen["session_id"] = session_id
            security_error = await agent.security.inspect(
                {"messages": [{"role": "user", "content": inspect_text}]},
                session_id,
                ip=_client_ip,
                key_prefix=_key_prefix,
            )
            if security_error:
                logger.warning(f"SecurityShield blocked embedding: {security_error}")
                MetricsTracker.track_injection_blocked()
                raise HTTPException(status_code=403, detail=security_error)

            # Resolve provider
            provider = _detect_embedding_provider(model)
            seen["provider"] = provider
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
            target_url, translated_body, translated_headers = (
                adapter.translate_embedding_request(
                    base_url,
                    body,
                    headers,
                )
            )

            # Forward request
            session = await agent._get_session()

            try:
                response = await adapter.request(
                    target_url, translated_body, translated_headers, session
                )
            except Exception as e:
                logger.error(f"Embedding request failed: {e}")
                raise HTTPException(
                    status_code=502, detail="Embedding upstream request failed"
                ) from e


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
                except (json.JSONDecodeError, KeyError) as e:
                    logger.warning("Embedding response translation skipped: %s", e)

            # Cost tracking. Embeddings is its own route — no downstream
            # enqueue runs (unlike /v1/chat/completions), so without persistence
            # here a crash before the next chat request would lose the charge.
            try:
                if hasattr(response, "body"):
                    usage = json.loads(response.body.decode()).get("usage", {})
                    tokens = usage.get("total_tokens", 0) or usage.get("prompt_tokens", 0)
                    cost_usd = estimate_cost(model, tokens, 0)
                    seen["tokens"], seen["cost_usd"] = int(tokens or 0), cost_usd
                    from core.model_resolver import known_model_names

                    MetricsTracker.track_usage(
                        endpoint="/v1/embeddings",
                        model=model,
                        prompt_tokens=tokens,
                        completion_tokens=0,
                        cost=cost_usd,
                        known_models=known_model_names(agent.config),
                    )
                    from proxy.budget import charge_and_persist

                    await charge_and_persist(agent, agent._budget_lock, cost_usd)
            except Exception as e:
                logger.warning("Embedding cost tracking skipped: %s", e)
        except HTTPException as stop:
            await _record(
                agent, request, token, seen, started, stop.status_code, refusal=stop.detail
            )
            raise

        await _record(agent, request, token, seen, started, response.status_code)
        return response

    return router

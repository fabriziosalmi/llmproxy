"""Chat completion route: /v1/chat/completions — the core proxy endpoint."""
import time
import asyncio
import logging

from fastapi import APIRouter, Request, HTTPException, Depends
from fastapi.security import APIKeyHeader

logger = logging.getLogger("llmproxy.routes.chat")

API_KEY_HEADER = APIKeyHeader(name="Authorization", auto_error=False)


def create_router(agent) -> APIRouter:
    router = APIRouter()

    @router.post("/v1/chat/completions")
    async def chat_completions(request: Request, api_key: str = Depends(API_KEY_HEADER)):
        # Lazy imports — avoids pulling in heavy deps (otel, sentry) at import time
        from core.metrics import MetricsTracker
        from core.tracing import TraceManager
        from core.webhooks import EventType

        if agent.config["server"]["auth"]["enabled"]:
            if not api_key:
                MetricsTracker.track_auth_failure("missing_key")
                asyncio.create_task(agent.webhooks.dispatch(EventType.AUTH_FAILURE, {"reason": "missing_key", "ip": request.client.host if request.client else "unknown"}))
                raise HTTPException(status_code=401, detail="Unauthorized: Missing API key")
            token = api_key.replace("Bearer ", "").strip()
            if not token:
                MetricsTracker.track_auth_failure("empty_token")
                raise HTTPException(status_code=401, detail="Unauthorized: Empty token")

            identity = None

            if agent.identity.enabled:
                try:
                    identity = agent.identity.verify_proxy_jwt(token)
                    if not identity:
                        identity = await agent.identity.verify_token(token)
                except ValueError as e:
                    MetricsTracker.track_auth_failure("jwt_invalid")
                    asyncio.create_task(agent.webhooks.dispatch(EventType.AUTH_FAILURE, {"reason": "jwt_invalid", "error": str(e)}))
                    raise HTTPException(status_code=401, detail=f"Identity: {e}")

            if identity and identity.verified:
                request.state.identity = identity
                request.state.user = identity.email or identity.subject
                request.state.roles = identity.roles
                if not agent.rbac.check_permission(identity.roles, "proxy:use"):
                    raise HTTPException(status_code=403, detail="Insufficient permissions")
                agent.rbac.set_user_roles(identity.subject, identity.email, identity.roles)
                await agent._add_log(
                    f"IDENTITY: {identity.provider} user={identity.email or identity.subject} roles={identity.roles}",
                    level="SECURITY"
                )
            else:
                valid_keys = agent._get_api_keys()
                if token not in valid_keys:
                    MetricsTracker.track_auth_failure("invalid_key")
                    asyncio.create_task(agent.webhooks.dispatch(EventType.AUTH_FAILURE, {"reason": "invalid_api_key", "ip": request.client.host if request.client else "unknown"}))
                    raise HTTPException(status_code=401, detail="Unauthorized: Invalid API key or JWT")

                if not agent.rbac.check_quota(token):
                    asyncio.create_task(agent.webhooks.dispatch(EventType.BUDGET_THRESHOLD, {"reason": "quota_exceeded", "key_prefix": token[:8] + "..."}))
                    raise HTTPException(status_code=402, detail="Enterprise Quota Exceeded for this API Key.")

            client_host = request.client.host if request.client else "0.0.0.0"
            ts_id = await agent.zt_manager.verify_tailscale_identity(client_host)
            if ts_id["status"] == "verified":
                await agent._add_log(f"ZT VERIFIED: {ts_id['user']} on {ts_id['node']}", level="SECURITY")
                request.state.user = getattr(request.state, 'user', None) or ts_id['user']
                request.state.node = ts_id['node']

        if not agent.proxy_enabled:
            raise HTTPException(status_code=503, detail="Proxy service is currently STOPPED.")

        start_time = time.time()
        try:
            # Use token as session_id when available; fall back to client IP to
            # avoid collapsing ALL anonymous users into a single trajectory bucket
            # (which would cross-contaminate threat scores across unrelated clients).
            session_id = token if 'token' in locals() else (request.client.host if request.client else "anon")
            body = await request.json()

            # Request deduplication via X-Idempotency-Key header
            idempotency_key = request.headers.get("X-Idempotency-Key")
            if idempotency_key:
                dedup_key = f"{session_id}:{idempotency_key}"
                response = await agent.deduplicator.execute_or_wait(
                    dedup_key, agent.proxy_request(request, body=body, session_id=session_id),
                )
            else:
                response = await agent.proxy_request(request, body=body, session_id=session_id)
            duration = time.time() - start_time
            MetricsTracker.track_request("POST", "/v1/chat/completions", response.status_code, duration)
            if agent.exporter:
                asyncio.create_task(agent.exporter.record({
                    "messages": body.get("messages", []),
                    "model": body.get("model", "auto"),
                    "latency_ms": round(duration * 1000, 1),
                    "status": response.status_code,
                }))
            # Token-based cost estimate using per-model pricing table.
            # Prefer actual usage fields from the response; fall back to
            # char-count heuristic (~4 chars/token) for streaming responses
            # where the body is not available as a single blob.
            from core.pricing import estimate_cost, estimate_cost_pre_flight
            from core.tokenizer import count_messages_tokens
            cost_usd = 0.0
            model_name = body.get("model", "")
            try:
                if hasattr(response, "body"):
                    import json as _json
                    usage = _json.loads(response.body).get("usage", {})
                    in_tok = usage.get("prompt_tokens") or count_messages_tokens(body.get("messages", []), model_name)
                    out_tok = usage.get("completion_tokens", 0)
                    cost_usd = estimate_cost(model_name, in_tok, out_tok)
                else:
                    # Streaming: estimate from prompt using tiktoken
                    est_tokens = count_messages_tokens(body.get("messages", []), model_name)
                    cost_usd = estimate_cost_pre_flight(model_name, est_tokens)
            except Exception:
                pass
            agent.total_cost_today += cost_usd
            budget_cfg = agent.config.get("budget", {})
            daily_limit = budget_cfg.get("daily_limit", 50.0)
            soft_limit = budget_cfg.get("soft_limit", 40.0)
            MetricsTracker.set_budget(agent.total_cost_today, daily_limit)
            # Persist daily budget to SQLite (batched, graceful-shutdown safe)
            agent.enqueue_write("budget:daily_total", agent.total_cost_today)
            if agent.total_cost_today >= soft_limit:
                asyncio.create_task(agent.webhooks.dispatch(EventType.BUDGET_THRESHOLD, {"consumed": agent.total_cost_today, "limit": daily_limit}))

            # Log spend + audit (async, non-blocking)
            import datetime as _dt
            _now = int(time.time())
            _date = _dt.date.today().isoformat()
            _key = (token[:8] + "...") if 'token' in locals() and token else ""
            # Extract metadata from response headers (set by rotator.proxy_request)
            _provider = ""
            _req_id = ""
            _in_tok = 0
            _out_tok = 0
            if response and hasattr(response, "headers"):
                _provider = response.headers.get("X-LLMProxy-Provider", "")
                _req_id = response.headers.get("X-LLMProxy-Request-Id", "")
            try:
                if hasattr(response, "body"):
                    _usage = __import__("json").loads(response.body).get("usage", {})
                    _in_tok = _usage.get("prompt_tokens", 0)
                    _out_tok = _usage.get("completion_tokens", 0)
            except Exception:
                pass
            _status = response.status_code if response and hasattr(response, "status_code") else 200

            async def _persist_logs():
                try:
                    await agent.store.log_spend(
                        ts=_now, date=_date, key_prefix=_key, model=model_name,
                        provider=_provider, prompt_tokens=_in_tok, completion_tokens=_out_tok,
                        cost_usd=cost_usd, latency_ms=round(duration * 1000, 1), status=_status,
                    )
                    await agent.store.log_audit(
                        ts=_now, req_id=_req_id, session_id=session_id[:16],
                        key_prefix=_key, model=model_name, provider=_provider,
                        status=_status, prompt_tokens=_in_tok, completion_tokens=_out_tok,
                        cost_usd=cost_usd, latency_ms=round(duration * 1000, 1),
                    )
                except Exception:
                    pass
            asyncio.create_task(_persist_logs())

            return response
        except Exception as e:
            duration = time.time() - start_time
            MetricsTracker.track_request("POST", "/v1/chat/completions", 500, duration)
            TraceManager.capture_exception(e)
            raise e

    return router

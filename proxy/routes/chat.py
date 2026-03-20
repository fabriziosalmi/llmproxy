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
            session_id = token if 'token' in locals() else "anonymous"
            body = await request.json()
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
            agent.total_cost_today += duration * 0.001
            budget_cfg = agent.config.get("budget", {})
            MetricsTracker.set_budget(agent.total_cost_today, budget_cfg.get("monthly_limit", 1000.0))
            # J.5: Persist daily budget to SQLite (async, non-blocking)
            asyncio.create_task(agent.store.set_state("budget:daily_total", agent.total_cost_today))
            if agent.total_cost_today >= budget_cfg.get("soft_limit", 800.0):
                asyncio.create_task(agent.webhooks.dispatch(EventType.BUDGET_THRESHOLD, {"consumed": agent.total_cost_today, "limit": budget_cfg.get("monthly_limit", 1000.0)}))
            return response
        except Exception as e:
            duration = time.time() - start_time
            MetricsTracker.track_request("POST", "/v1/chat/completions", 500, duration)
            TraceManager.capture_exception(e)
            asyncio.create_task(agent.chatbot.track_error())
            raise e

    return router

"""Identity routes: SSO/OIDC config, current user, token exchange."""
from fastapi import APIRouter, Request, HTTPException, Depends
from fastapi.security import APIKeyHeader

API_KEY_HEADER = APIKeyHeader(name="Authorization", auto_error=False)


def create_router(agent) -> APIRouter:
    router = APIRouter()

    @router.get("/api/v1/identity/config")
    async def get_identity_config():
        if not agent.identity.enabled:
            return {"enabled": False, "providers": []}
        providers = []
        for name, p in agent.identity.providers.items():
            providers.append({
                "name": p.name,
                "client_id": p.client_id,
                "issuer": p.issuer,
            })
        return {"enabled": True, "providers": providers}

    @router.get("/api/v1/identity/me")
    async def get_identity(request: Request, api_key: str = Depends(API_KEY_HEADER)):
        if not api_key:
            return {"authenticated": False}
        token = api_key.replace("Bearer ", "").strip()
        if not token:
            return {"authenticated": False}
        if agent.identity.enabled:
            identity = agent.identity.verify_proxy_jwt(token)
            if not identity:
                try:
                    identity = await agent.identity.verify_token(token)
                except ValueError:
                    identity = None
            if identity:
                return {
                    "authenticated": True,
                    "provider": identity.provider,
                    "email": identity.email,
                    "name": identity.name,
                    "roles": identity.roles,
                    "permissions": list(agent.rbac.get_permissions_for_roles(identity.roles)),
                }
        valid_keys = agent._get_api_keys()
        if token in valid_keys:
            return {
                "authenticated": True,
                "provider": "api_key",
                "roles": ["user"],
                "permissions": list(agent.rbac.get_permissions_for_roles(["user"])),
            }
        return {"authenticated": False}

    @router.post("/api/v1/identity/exchange")
    async def exchange_token(request: Request):
        if not agent.identity.enabled:
            raise HTTPException(status_code=501, detail="SSO not enabled")
        data = await request.json()
        external_token = data.get("token", "")
        if not external_token:
            raise HTTPException(status_code=400, detail="Missing token")
        try:
            identity = await agent.identity.verify_token(external_token)
        except ValueError as e:
            raise HTTPException(status_code=401, detail=str(e))
        if not identity:
            raise HTTPException(status_code=401, detail="Unrecognized JWT provider")
        ttl = agent.config.get("identity", {}).get("session_ttl", 3600)
        proxy_token = agent.identity.generate_proxy_jwt(identity, ttl=ttl)
        agent.rbac.set_user_roles(identity.subject, identity.email, identity.roles)
        return {
            "token": proxy_token,
            "expires_in": ttl,
            "identity": {
                "email": identity.email,
                "name": identity.name,
                "roles": identity.roles,
                "provider": identity.provider,
            },
        }

    return router

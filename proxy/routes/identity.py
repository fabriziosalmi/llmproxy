"""Identity routes: SSO/OIDC config, current user, token exchange."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import APIKeyHeader

from core.auth_policy import auth_enabled
from core.revocation import STATE_KEY as REVOCATIONS_KEY
from proxy.routes.deps import IdentityAgent

logger = logging.getLogger("llmproxy.routes.identity")

API_KEY_HEADER = APIKeyHeader(name="Authorization", auto_error=False)


def create_router(agent: IdentityAgent) -> APIRouter:
    router = APIRouter()

    @router.get("/api/v1/identity/config")
    async def get_identity_config():
        # proxy_auth_enabled is the SECOND axis the UI needs: SSO (identity)
        # and API-key auth (server.auth) are independent. The UI must know
        # whether ANY credential is required so it can skip the overlay
        # entirely in fully-open dev mode.
        proxy_auth_enabled = (
            auth_enabled(agent.config)
        )
        if not agent.identity.enabled:
            return {
                "enabled": False,
                "providers": [],
                "proxy_auth_enabled": proxy_auth_enabled,
            }
        providers = []
        for _name, p in agent.identity.providers.items():
            providers.append(
                {
                    "name": p.name,
                    "client_id": p.client_id,
                    "issuer": p.issuer,
                }
            )
        return {
            "enabled": True,
            "providers": providers,
            "proxy_auth_enabled": proxy_auth_enabled,
        }

    @router.get("/api/v1/identity/me")
    async def get_identity(request: Request, api_key: str = Depends(API_KEY_HEADER)):
        if not api_key:
            return {"authenticated": False}
        from proxy.auth_helpers import parse_bearer

        token = parse_bearer(api_key)
        if not token:
            return {"authenticated": False}
        if agent.identity.enabled:
            try:
                identity = agent.identity.verify_proxy_jwt(token)
            except ValueError:
                identity = None
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
                    "permissions": list(
                        agent.rbac.get_permissions_for_roles(identity.roles)
                    ),
                }
        if agent._verify_api_key(token):
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
            # Log the precise validation reason server-side for ops, but
            # return a generic message to the caller — leaking "Token
            # expired" vs "Invalid issuer" lets attackers probe which
            # validation step failed.
            logger.warning(f"Token exchange validation failed: {e}")
            raise HTTPException(status_code=401, detail="Invalid token") from e
        if not identity:
            raise HTTPException(status_code=401, detail="Invalid token")
        ttl = agent.config.get("identity", {}).get("session_ttl", 3600)
        proxy_token = agent.identity.generate_proxy_jwt(identity, ttl=ttl)
        await agent.rbac.set_user_roles(
            identity.subject, identity.email, identity.roles
        )
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

    @router.post("/api/v1/identity/revoke")
    async def revoke_sessions(request: Request):
        """End proxy-issued sessions before they expire (administrator only).

        Body: ``{"subject": "<sub>"}`` revokes every session for that person
        issued up to now; ``{"jti": "<id>", "exp": <epoch, optional>}`` revokes
        one token. A fresh login against the identity provider afterwards gets a
        new session: this ends access that exists, it does not decide who may
        sign in again.
        """
        data = await request.json()
        if not isinstance(data, dict):
            raise HTTPException(status_code=400, detail="Body must be an object")
        subject, jti = data.get("subject"), data.get("jti")
        if bool(subject) == bool(jti):
            raise HTTPException(
                status_code=400, detail="Provide exactly one of 'subject' or 'jti'"
            )
        target = subject or jti
        if not isinstance(target, str) or len(target) > 256:
            raise HTTPException(status_code=400, detail="Invalid identifier")

        revocations = agent.identity.revocations
        if subject:
            revoked_at = revocations.revoke_subject(subject)
            result = {"status": "revoked", "subject": subject, "revoked_at": revoked_at}
        else:
            exp = data.get("exp")
            if exp is not None and not isinstance(exp, (int, float)):
                raise HTTPException(status_code=400, detail="'exp' must be a number")
            revocations.revoke_jti(str(jti), exp)
            result = {"status": "revoked", "jti": jti}
        # Persist before answering: a revocation that a restart can undo is not one.
        await agent.store.set_state(REVOCATIONS_KEY, revocations.dump())
        await agent._add_log(
            f"IDENTITY: sessions revoked ({'subject' if subject else 'jti'}={target[:64]})",
            level="SECURITY",
        )
        return result

    @router.get("/api/v1/identity/revocations")
    async def revocation_summary():
        """How many tokens and subjects are currently revoked."""
        return agent.identity.revocations.summary()

    return router

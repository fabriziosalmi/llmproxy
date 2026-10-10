# API: Identity & SSO

Authentication and authorization endpoints.

## Current User

```
GET /api/v1/identity/me
```

Returns current user identity, roles, and permissions (derived from JWT or API key).

**Response:**
```json
{
  "email": "user@example.com",
  "name": "Jane Doe",
  "provider": "google",
  "roles": ["user"],
  "permissions": ["proxy:use", "registry:read", "chat", "logs:read"]
}
```

## Token Exchange

```
POST /api/v1/identity/exchange
```

Exchange an external OIDC JWT for an internal proxy session token.

**Request:**
```json
{
  "token": "eyJhbGciOiJSUzI1NiIs...",
  "provider": "google"
}
```

**Response:**
```json
{
  "token": "eyJhbGciOiJIUzI1NiIs...",
  "expires_in": 3600,
  "identity": {
    "email": "user@example.com",
    "name": "A User",
    "roles": ["user"],
    "provider": "google"
  }
}
```

The internal token should be used as Bearer token for subsequent API calls.

## Revoking Sessions

A proxy session token (the `token` returned by the exchange above) carries the roles it
was issued with and is trusted until it expires (`identity.session_ttl`, default
3600 s). To end access sooner, revoke it. Each token has a random `jti` claim.

```
POST /api/v1/identity/revoke
```

Permission `users:manage` (administrators). Provide exactly one of:

```json
{"subject": "user-123"}
```

Revokes **every** session for that subject issued up to now. A token minted
afterwards, by a fresh exchange against the identity provider, is not affected:
this ends access that exists; whether the person can sign in again is decided by
the identity provider and the role mapping.

```json
{"jti": "9f2c...", "exp": 1790000000}
```

Revokes one token. `exp` (the token's expiry, optional) lets the entry be
dropped when the token could no longer be used anyway.

Response: `{"status": "revoked", "subject": "...", "revoked_at": 1789999999}` or
`{"status": "revoked", "jti": "..."}`. The list is saved before the response is
sent and reloaded at startup, so a restart does not un-revoke anyone. A revoked
token is refused on the next request (`401` on the data plane and the control
plane, `authenticated: false` from `GET /api/v1/identity/me`).

```
GET /api/v1/identity/revocations
```

Counts only: `{"tokens": N, "subjects": N}`.

Revoking does not touch the identity provider, and a token minted before this
feature existed has no `jti`, so it can only be revoked by subject.

## SSO Config

```
GET /api/v1/identity/config
```

Returns the public SSO provider list for the frontend OAuth flow.

**Response:**
```json
{
  "enabled": true,
  "providers": [
    {"name": "google", "client_id": "..."},
    {"name": "microsoft", "client_id": "..."}
  ]
}
```

## RBAC Roles

```
GET /api/v1/rbac/roles
```

Returns the complete role permission matrix.

| Role | proxy:use | registry:read | registry:write | chat | logs:read | plugins:manage | users:manage | budget:manage |
|------|-----------|--------------|----------------|------|-----------|----------------|--------------|---------------|
| admin | yes | yes | yes | yes | yes | yes | yes | yes |
| operator | yes | yes | yes | yes | yes | yes | - | - |
| user | yes | - | - | yes | - | - | - | - |
| viewer | - | yes | - | - | yes | - | - | - |

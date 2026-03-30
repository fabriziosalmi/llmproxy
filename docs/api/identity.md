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
  "session_token": "eyJhbGciOiJIUzI1NiIs...",
  "expires_in": 3600,
  "roles": ["user"]
}
```

The internal token should be used as Bearer token for subsequent API calls.

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
| user | yes | yes | - | yes | yes | - | - | - |
| viewer | - | yes | - | - | yes | - | - | - |

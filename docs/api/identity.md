# API: Identity & SSO

Endpoints for signing in with an identity provider and for inspecting the
caller's roles.

Identity is **off by default** (`identity.enabled: false` in `config.yaml`). With
it off, callers authenticate with API keys only, `POST /api/v1/identity/exchange`
answers `501`, and provider tokens are not accepted anywhere. See
[Identity](/security/identity) for the configuration.

`/api/v1/identity/config`, `/exchange` and `/me` need no credential. The other
routes on this page are control-plane routes; see the
[Admin API](/api/admin#authentication-and-permissions).

## Current User

```
GET /api/v1/identity/me
```

Describes the credential sent in the `Authorization` header. The route always
answers `200`.

With no header, or a credential that is not valid:

```json
{"authenticated": false}
```

With a valid provider JWT or proxy session token (identity on):

```json
{
  "authenticated": true,
  "provider": "google",
  "email": "user@example.com",
  "name": "Jane Doe",
  "roles": ["user"],
  "permissions": ["proxy:use", "chat:use"]
}
```

With a key from `LLM_PROXY_API_KEYS`:

```json
{
  "authenticated": true,
  "provider": "api_key",
  "roles": ["user"],
  "permissions": ["proxy:use", "chat:use"]
}
```

The order of `permissions` is not fixed. The key answer always reports the `user`
role, whatever the key can do elsewhere: on the control plane an admin key acts
with the `admin` role. A key that is only in `LLM_PROXY_ADMIN_KEYS` is reported as
`{"authenticated": false}` here, because this route checks the inference keys.

## Token Exchange

```
POST /api/v1/identity/exchange
```

Exchanges a provider's ID token for a proxy session token.

**Request:**
```json
{
  "token": "eyJhbGciOiJSUzI1NiIs..."
}
```

The provider is found from the token's `iss` claim; the body has no other field.

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

`expires_in` is `identity.session_ttl` (default 3600 seconds). Send the returned
token as `Authorization: Bearer <token>` on later calls.

| Status | Cause |
|-------:|-------|
| 400 | No `token` in the body |
| 401 | The token is not a JWT from a configured provider, or its signature, audience, issuer or expiry does not verify, or its subject has been revoked. The body is always `{"detail": "Invalid token"}` |
| 501 | Identity is off |

The session token is signed with `LLM_PROXY_IDENTITY_SECRET` (HS256). The variable
must be set for the exchange to work.

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

Revokes **every** token for that subject issued up to now: proxy session tokens
and the provider's own tokens. A token issued afterwards, by a fresh sign-in at
the identity provider, is not affected: this ends access that exists; whether the
person can sign in again is decided by the identity provider and the role mapping.

```json
{"jti": "9f2c...", "exp": 1790000000}
```

Revokes one token. `exp` (the token's expiry, optional) lets the entry be
dropped when the token could no longer be used anyway.

Response: `{"status": "revoked", "subject": "...", "revoked_at": 1789999999}` or
`{"status": "revoked", "jti": "..."}`. `400` if both or neither of `subject` and
`jti` are given, or the identifier is longer than 256 characters. The list is
saved before the response is sent and reloaded at startup, so a restart does not
un-revoke anyone. A revoked token is refused on the next request (`401` on the
data plane and the control plane, `authenticated: false` from
`GET /api/v1/identity/me`).

A subject revocation is kept for seven days; a `jti` revocation until the given
`exp` plus five minutes, or for seven days when no `exp` is given.

```
GET /api/v1/identity/revocations
```

Permission `logs:read`. Counts only: `{"tokens": N, "subjects": N}`.

Revoking does not touch the identity provider. A token without a `jti` claim can
only be revoked by subject.

## SSO Config

```
GET /api/v1/identity/config
```

Public. Tells the admin UI whether to offer sign-in and with which providers.

With identity off (the default):

```json
{"enabled": false, "providers": [], "proxy_auth_enabled": true}
```

With identity on:

```json
{
  "enabled": true,
  "providers": [
    {"name": "google", "client_id": "...", "issuer": "https://accounts.google.com"}
  ],
  "proxy_auth_enabled": true
}
```

`proxy_auth_enabled` is `server.auth.enabled`: whether any credential is required.
Only providers that have a client id configured are listed.

## RBAC Roles

```
GET /api/v1/rbac/roles
```

Permission `users:manage`. Returns the role-to-permission matrix as
`{"<role>": ["<permission>", ...]}`. The four roles (`core/rbac.py`):

| Permission | admin | operator | user | viewer |
|------------|:-----:|:--------:|:----:|:------:|
| `proxy:use` | yes | yes | yes | - |
| `chat:use` | yes | yes | yes | - |
| `chat:compare` | yes | yes | - | - |
| `proxy:toggle` | yes | yes | - | - |
| `proxy:config` | yes | - | - | - |
| `registry:read` | yes | yes | - | yes |
| `registry:write` | yes | yes | - | - |
| `registry:delete` | yes | - | - | - |
| `logs:read` | yes | yes | - | yes |
| `logs:clear` | yes | yes | - | - |
| `plugins:manage` | yes | yes | - | - |
| `features:toggle` | yes | yes | - | - |
| `users:manage` | yes | - | - | - |
| `budget:manage` | yes | - | - | - |

`proxy:use` is what the `/v1/` routes require of a signed-in user. The
control-plane routes require the permission the [Admin API](/api/admin) names for
each. `chat:use`, `chat:compare`, `registry:delete` and `budget:manage` are
declared but no route requires them: deleting an endpoint needs `registry:write`.

A user whose token carries no recognised role gets `identity.default_role`
(`user` by default), which can call `/v1/` and nothing on the control plane.

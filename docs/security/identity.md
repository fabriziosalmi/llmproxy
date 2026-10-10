# Identity & SSO

How callers are authenticated, and how a signed-in user gets a role.

API keys are the default. OIDC/JWT sign-in is optional and **off by default**
(`identity.enabled: false`).

## Auth model

While authentication is on (`server.auth.enabled`, the default), all paths under
`/api/v1/*` and `/admin/*`, and `/metrics`, are refused without a valid credential
by a middleware in `proxy/app_factory.py`, before any route handler runs. The
paths reachable without credentials are `/health`, `/ready`,
`/api/v1/identity/config`, `/api/v1/identity/exchange` and `/api/v1/identity/me`.
The `/v1/*` routes authenticate in the route handler.

See [Security overview](/security/overview) for where this sits in the request
path.

## Credentials

A request's bearer credential is checked in this order.

On `/v1/*`:

1. **JWT**, only when `identity.enabled` is true: a proxy session token, or a
   provider's ID token verified against the provider's JWKS. The role must hold
   `proxy:use` (`403` otherwise).
2. **API key** from `LLM_PROXY_API_KEYS`.

On the control plane:

1. **Admin key** from `LLM_PROXY_ADMIN_KEYS`. If that variable is not set, the
   keys in `LLM_PROXY_API_KEYS` are accepted instead. A key acts with the `admin`
   role.
2. **JWT**, only when `identity.enabled` is true. The caller acts with the roles
   in the token.
3. **Admin JWT**, only when `server.admin_auth.oidc_enabled` is true: a token
   verified with `server.admin_auth.jwt_secret`. It acts with the `admin` role.

**Tailscale is not a credential.** After a `/v1/` request has authenticated, the
proxy asks the local Tailscale daemon (LocalAPI `whois` over its Unix socket, one
second timeout) who the peer address belongs to. If it answers, the user and node
are written to the log. The lookup never grants or denies access, and nothing
happens when Tailscale is not installed.

Mutual TLS is not implemented.

## OIDC Providers

Configured in `config.yaml`:

```yaml
identity:
  enabled: true
  default_role: "user"
  providers:
    - name: google
      client_id_env: "OIDC_GOOGLE_CLIENT_ID"
    - name: microsoft
      client_id_env: "OIDC_MICROSOFT_CLIENT_ID"
    - name: apple
      client_id_env: "OIDC_APPLE_CLIENT_ID"
  session_ttl: 3600
```

For `google`, `microsoft` and `apple` the issuer and the JWKS address are built
in. Any other provider needs `issuer`, and `jwks_uri` unless it is
`<issuer>/.well-known/jwks.json`. No discovery document is fetched. A provider
with no client id (from `client_id_env`, or `client_id`) is skipped with a warning.

Per-provider keys: `name`, `client_id_env`, `client_id`, `issuer`, `jwks_uri`,
`audience` (defaults to the client id), `email_claim`, `name_claim`, `roles_claim`.

A token is matched to a provider by comparing its `iss` claim with the provider's
`issuer`, exactly. The built-in Microsoft issuer is
`https://login.microsoftonline.com/common/v2.0`; if your tokens carry a
tenant-specific issuer, set `issuer` on the provider entry.

Verification checks the signature (RS256 or ES256), the audience, the issuer and
the expiry. Signing keys are cached for one hour; a key id that is not in the
cached set triggers a new fetch. A JWKS fetch that takes longer than 5 seconds
(10 seconds including the wait for a fetch already in progress) fails the request
with `401`.

## Token Exchange

1. The admin UI opens the provider's authorisation page in a popup.
2. The user signs in; the provider returns an `id_token`.
3. The UI calls `POST /api/v1/identity/exchange` with that token.
4. The proxy verifies it and issues a session token: a JWT signed with
   `LLM_PROXY_IDENTITY_SECRET` (HS256), valid for `identity.session_ttl` seconds
   (default 3600), carrying the user's roles.
5. The UI stores the session token in `localStorage` and sends it as a bearer
   token.

`LLM_PROXY_IDENTITY_SECRET` must be set for the exchange to work. A session token
keeps the roles it was issued with until it expires; to end one earlier, see
[Revoking sessions](/api/identity#revoking-sessions).

## RBAC

Four roles, defined in `core/rbac.py`:

| Role | Permissions |
|------|-------------|
| **admin** | All fourteen |
| **operator** | `proxy:use`, `proxy:toggle`, `registry:read`, `registry:write`, `chat:use`, `chat:compare`, `logs:read`, `logs:clear`, `plugins:manage`, `features:toggle` |
| **user** | `proxy:use`, `chat:use` |
| **viewer** | `registry:read`, `logs:read` |

`user` can call `/v1/` and nothing on the control plane. `viewer` can read the
registry and the logs and cannot call `/v1/`. The full matrix and the permission
each route needs are in the [Identity API](/api/identity#rbac-roles) and the
[Admin API](/api/admin).

### How a user gets a role

In this order:

1. `identity.role_mappings`: if the token's email is listed, those roles apply.
2. The token's roles claim (`roles` by default): the values that are one of the
   four role names apply.
3. Otherwise `identity.default_role` (`user` by default).

```yaml
identity:
  role_mappings:
    "admin@example.com": ["admin"]
    "ops@example.com": ["operator"]
```

Step 2 means a provider that puts `admin` in the roles claim of a token makes
its holder an administrator of the proxy. Use a provider and a client id whose
tokens you control.

The subject, email and roles of each signed-in caller are recorded in the store's
`user_roles` table. The table is a record, read by the
[GDPR export and erasure](/api/admin#data-protection-gdpr) routes; it is not
consulted to authorise a request.

## Sign-in in the admin UI

`ui/services/auth.js`:

1. The UI reads `GET /api/v1/identity/config`.
2. A provider button opens the provider's authorisation page in a popup.
3. `oauth-callback.html` passes the `id_token` back with `postMessage`.
4. The UI exchanges it for a session token.
5. If a credential is required and none is valid, a login overlay is shown.
   Entering an API key by hand is always available.

![Admin UI settings](/screenshots/soc-settings.png)

# Identity & SSO

LLMProxy supports stateless multi-provider OIDC/JWT authentication with role-based access control.

## Authentication Chain

LLMProxy tries authentication methods in order:

1. **JWT** — Bearer token verified via OIDC JWKS
2. **API Key** — Static key from `LLM_PROXY_API_KEYS`
3. **Tailscale** — Machine/user identity via LocalAPI socket

## OIDC Providers

Configured via `config.yaml`:

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

Providers are auto-configured via well-known OIDC discovery endpoints. JWKS keys are cached with 1-hour TTL and auto-refreshed on rotation.

## Token Exchange

The OAuth flow:

1. Frontend opens provider OAuth popup
2. User authenticates, receives `id_token`
3. Frontend calls `POST /api/v1/identity/exchange` with the external JWT
4. LLMProxy verifies the JWT via JWKS, issues an internal proxy session token
5. Internal token stored in `localStorage`, sent as Bearer on subsequent requests

## RBAC

Four built-in roles with granular permissions:

| Role | Key Permissions |
|------|----------------|
| **admin** | Full access: proxy, registry, chat, logs, plugins, users, budget |
| **operator** | Proxy toggle, registry write, plugins manage, features toggle |
| **user** | Proxy use, registry read, chat, logs read |
| **viewer** | Registry read, logs read only |

### Role Mapping

Map email addresses to roles:

```yaml
identity:
  role_mappings:
    "admin@example.com": ["admin"]
    "ops@example.com": ["operator"]
```

Roles are also persisted in the SQLite `user_roles` table.

## Zero-Trust

- **Tailscale LocalAPI**: Verifies machine/user identity via Unix socket (`whois` API)
- **URL Injection Prevention**: All user-supplied IPs/URLs escaped via `urllib.parse.quote()`

## Frontend OAuth

The SOC dashboard (`ui/services/auth.js`) implements a popup-based OAuth flow:

1. Click provider button → popup opens OIDC authorize URL
2. `oauth-callback.html` relays token via `postMessage`
3. Token exchange converts external JWT to internal session
4. If identity is enabled and no valid session exists, a glassmorphism login overlay is shown
5. Manual API key entry available as fallback

![SOC Settings](/screenshots/soc-settings.png)

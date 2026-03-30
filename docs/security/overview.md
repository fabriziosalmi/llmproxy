# Security Overview

LLMProxy implements defense-in-depth with multiple security layers. Every request passes through the full pipeline before reaching any LLM provider.

## Security Pipeline

```
Request → Global Auth Middleware → ASGI Firewall → SecurityShield → Plugin Rings → LLM Provider
               │                       │                  │               │
               ├─ Deny-all /api/v1/*   ├─ Pattern scan    ├─ Injection     ├─ Budget guard
               ├─ Deny-all /admin/*    └─ Instant 403     │  scoring       ├─ Loop breaker
               ├─ Deny /metrics                           ├─ PII masking   ├─ Rate limiter
               └─ Whitelist: /health,                     └─ Trajectory    └─ Topic blocklist
                  /api/v1/identity/*                         detection
```

## Layers

### 0. Global Auth Middleware (Fail-Closed) — v1.10.0

The outermost security layer in `proxy/app_factory.py`. Implements **deny-by-default** for all admin-class paths before any route handler executes.

**Protected prefixes** (require valid Bearer token):
- `/api/v1/*` — all API endpoints
- `/admin/*` — all admin endpoints
- `/metrics` — Prometheus endpoint (timing/volume side-channels)

**Public whitelist** (`_PUBLIC_EXACT`, the complete list):
- `/health` — liveness probe
- `/api/v1/identity/config` — SSO provider discovery
- `/api/v1/identity/exchange` — token exchange (JWT validated inside the route)
- `/api/v1/identity/me` — identity check (returns `{"authenticated": false}` for unauthenticated callers)

**API docs** (`/docs`, `/redoc`, `/openapi.json`) are disabled when auth is enabled — they would expose a full endpoint map before authentication.

Any new route added under a protected prefix is **automatically denied** until explicitly whitelisted. This eliminates the structural failure mode where forgetting `_check_admin_auth()` produces an instant CVE.

Per-route `_check_admin_auth()` closures are retained as defence-in-depth.

### 1. ASGI Firewall

Byte-level L7 filtering at the ASGI middleware layer. Scans raw request bodies for 11 injection patterns and terminates malicious requests with instant 403 — before any parsing or LLM cost is incurred.

[Read more →](/security/firewall)

### 2. SecurityShield

Deep inspection layer running pre-INGRESS in the request chain:

- **Injection Scoring**: Regex-based threat scoring with configurable threshold
- **PII Detection & Masking**: Dual-mode — Presidio NLP (18 entity types) + regex fallback
- **Multi-turn Trajectory Detection**: Tracks prompt scores per session, detects escalating jailbreak attempts

[Read more →](/security/injection-scoring)

### 3. Identity & Access Control

Stateless multi-provider OIDC/JWT verification with RBAC:

- **Providers**: Google, Microsoft, Apple via OIDC discovery
- **Auth chain**: JWT → API key → Tailscale identity
- **RBAC**: 4 roles (admin, operator, user, viewer) with granular permissions

[Read more →](/security/identity)

### 4. Plugin Security

- **AST Scanning**: All Python plugins are statically analyzed before loading — blocks `os`, `subprocess`, `exec`, `eval`
- **WASM Sandbox**: Untrusted plugins run in memory-safe WASM VMs via Extism
- **Timeout Enforcement**: Every plugin runs under strict `asyncio.wait_for()` timeout

### 5. Network Security

- **TLS 1.2+**: Configurable TLS with cert/key files
- **Zero-Trust**: Tailscale LocalAPI integration for machine/user identity verification
- **URL Injection Prevention**: All user-supplied URLs are escaped via `urllib.parse.quote()`
- **Rate Limiting**: Per-IP/per-key token bucket middleware (O(1) LRU eviction via `OrderedDict`)

### 6. Webhook Security

- **HMAC-SHA256 signing**: `X-Webhook-Signature: sha256=<hex>` header on every delivery when `secret` is configured
- **SSRF guard**: `_SSRFBlockingResolver` validates resolved IPs at aiohttp TCP connect time — after every DNS lookup — blocking private/reserved ranges (loopback, RFC-1918, 169.254/16, IPv6 ULA/link-local). Prevents DNS rebinding. Fail-closed on DNS failure.

### 7. Budget Integrity

- **Delta-based accounting**: Concurrent streaming requests accumulate cost deltas independently; the rotator adds each delta atomically under `budget_lock`, preventing lost-update races where concurrent streams silently overwrite each other's charges.

## Security Configuration

```yaml
security:
  enabled: true
  max_payload_size_kb: 512
  max_messages: 50
  link_sanitization:
    enabled: true
    blocked_domains: ["malicious-site.com"]

rate_limiting:
  enabled: true
  requests_per_minute: 60
```

# Security Overview

LLMProxy implements defense-in-depth with multiple security layers. Every request passes through the full pipeline before reaching any LLM provider.

## Security Pipeline

```
Request → ASGI Firewall → SecurityShield → Plugin Rings → LLM Provider
              │                  │               │
              ├─ Pattern scan    ├─ Injection     ├─ Budget guard
              └─ Instant 403     │  scoring       ├─ Loop breaker
                                 ├─ PII masking   ├─ Rate limiter
                                 └─ Trajectory    └─ Topic blocklist
                                    detection
```

## Layers

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

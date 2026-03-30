# Security Policy

## Reporting Security Vulnerabilities

**Please do NOT open public GitHub issues for security vulnerabilities.**

If you discover a security vulnerability in LLMProxy, please report it responsibly:

1. **Email**: Send details to **security@llmproxy.dev** (or open a private security advisory on GitHub)
2. **Include**: Description, reproduction steps, affected versions, and potential impact
3. **Encrypt** (optional): Use our PGP key available at `/.well-known/security.txt`

## Response Timeline

| Stage | Target |
|-------|--------|
| Acknowledgment | 24 hours |
| Initial assessment | 72 hours |
| Patch development | 7 days (critical), 30 days (non-critical) |
| Public disclosure | After patch release + reasonable adoption window |

## Scope

### In Scope
- LLMProxy core (`core/`, `proxy/`, `store/`)
- Default plugins (`plugins/default/`)
- Marketplace plugins (`plugins/marketplace/`)
- Configuration parsing and validation
- Authentication and authorization (API keys, OIDC/JWT, mTLS)
- Security pipeline (injection detection, PII masking, firewall)
- Docker image and supply chain integrity

### Out of Scope
- Third-party dependencies (report upstream; we'll assess impact)
- WASM plugin sandbox escapes (report to [Extism](https://github.com/extism/extism))
- Upstream LLM provider vulnerabilities
- Social engineering attacks

## Security Architecture

LLMProxy implements defense-in-depth with 6 layers:

1. **ASGI Byte-Level Firewall** — Binary/encoding attack detection before parsing
2. **Payload Size Guard** — Content-Length enforcement before JSON parsing (DoS protection)
3. **SecurityShield** — Prompt injection scoring, trajectory analysis, cross-session correlation
4. **5-Ring Plugin Pipeline** — Ingress auth, pre-flight budget/PII, routing, post-flight sanitization
5. **Rate Limiting** — Per-IP/per-key token bucket with automatic eviction
6. **Circuit Breakers** — Upstream failure isolation with automatic recovery

## Known Limitations

- Semantic injection detection uses lexical similarity (not full NLU)
- ASGI firewall is pattern-based; novel encoding schemes may bypass detection
- PII masking relies on regex + Presidio NLP; domain-specific PII may require custom patterns
- Plugin AST scanning is not a security sandbox; use WASM runtime for untrusted plugins

## Supported Versions

| Version | Supported |
|---------|-----------|
| 1.10.x  | Yes       |
| 1.9.x   | Yes       |
| < 1.9   | No        |

## Security Updates

Security patches are released as point versions (e.g., 1.7.2) and announced via:
- GitHub Releases
- CHANGELOG.md
- Security advisory (for critical vulnerabilities)

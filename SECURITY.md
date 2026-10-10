# Security Policy

## Reporting Security Vulnerabilities

**Please do NOT open public GitHub issues for security vulnerabilities.**

Report a vulnerability by email to <fabrizio.salmi@gmail.com>.

Include a description, reproduction steps, the affected versions and the potential impact.

## Response

No response time is committed. A fix is released as a new patch version and listed in `CHANGELOG.md`.

## Scope

### In Scope
- LLMProxy core (`core/`, `proxy/`, `store/`)
- Default plugins (`plugins/default/`)
- Marketplace plugins (`plugins/marketplace/`)
- Configuration parsing and validation
- Authentication and authorization (API keys, OIDC/JWT)
- Security pipeline (byte firewall, injection detection, PII masking, tool policy)
- Docker image and supply chain integrity

### Out of Scope
- Third-party dependencies (report upstream; we'll assess impact)
- WASM plugin sandbox escapes (report to [Extism](https://github.com/extism/extism)); the WASM runtime is not installed in the published image
- Upstream LLM provider vulnerabilities
- Social engineering attacks

## Security Architecture

A request passes these controls in order. Defaults are those of the shipped `config.yaml`.

1. **Admission control** — bounds the `/v1/*` requests in flight; beyond the limit and its queue, 503.
2. **Rate limiter** — token bucket per bearer token, or per IP without one. Off by default.
3. **Byte-level firewall** — runs before authentication. Enforces the body-size limit (512 KiB, 413), a deadline for receiving the body (30 s, 408) and a nesting-depth limit (64, 400), then scans the body against 178 signatures loaded from `data/signatures.yaml`, also after Unicode normalisation and base64, hex and ROT13 decoding. A match is refused with 403.
4. **CORS** — by default only `http://localhost:<port>` and `http://127.0.0.1:<port>` are allowed origins.
5. **Payload size guard** — refuses by `Content-Length` before the body is parsed.
6. **Control-plane authentication** — `/api/v1/*`, `/admin/*` and `/metrics` require an admin key, or a JWT whose roles hold the permission the route needs.
7. **Data-plane authentication** — `/v1/*` routes check the inference key, or an identity token when `identity.enabled` is true, in the route handler.
8. **SecurityShield** — injection scoring, per-session trajectory and cross-request correlation by IP and key.
9. **Plugin rings** — Ingress, Pre-Flight, Routing, Post-Flight, Background. A failing plugin refuses the request in the first three rings and is skipped in the last two, by default.
10. **Circuit breakers** — per upstream endpoint.

Security response headers (CSP, `X-Frame-Options`, `X-Content-Type-Options` and others) are set by a middleware that sits inside controls 1–4, so refusals issued by those controls do not carry them.

## Known Limitations

- Injection detection is lexical: byte signatures, regular expressions and character-trigram similarity against a corpus. A paraphrase with no lexical overlap, or an encoding the firewall does not decode, is not detected.
- PII masking uses regular expressions by default. Presidio is used when it is installed; it is not in the published image. Masking applies to chat messages, not to `/v1/embeddings`.
- Plugin AST scanning is not a security sandbox. The WASM runtime for untrusted plugins (Extism) is not in the published image.
- If `LLM_PROXY_ADMIN_KEYS` is unset, every inference key is accepted on the control plane. The proxy warns at startup.
- The proxy is a single instance. The daily budget, session scoring, the kill switch and feature toggles are per process.
- Without `LLM_PROXY_AUDIT_KEY` the audit chain is SHA-256, which someone who can write the database can recompute. With the key, removal of the newest rows is detectable only against a chain head recorded outside the database. Firewall blocks, 401 responses, rate-limit 429 responses and control-plane changes are not rows of the chain.
- mTLS is not implemented. TLS on the listener is off in the shipped `config.yaml`.
- Response signing is off unless a signing key is configured, and streamed responses are never signed.

## Supported Versions

Only the latest release is supported.

## Security Updates

Security fixes are released as patch versions and announced via:
- GitHub Releases
- CHANGELOG.md

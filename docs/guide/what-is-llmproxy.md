# What is LLMProxy?

LLMProxy is a **security-first proxy** for Large Language Models. It sits between your applications and LLM providers, adding layered security, intelligent routing, cost controls, and real-time monitoring.

## Architecture Overview

The request pipeline processes every LLM call through 10 security layers:

1. **Multi-Provider Translation** — 15 providers with automatic request/response format translation
2. **Cross-Provider Fallback** — Configurable fallback chains (e.g. GPT-4o fails → Claude Sonnet → Gemini Pro)
3. **Smart Routing** — EMA-weighted endpoint selection based on latency and success rate
4. **ASGI Firewall** — Byte-level L7 request filtering
5. **SecurityShield** — Injection scoring, PII masking, trajectory detection
6. **Ring Plugin Pipeline** — 5-ring plugin engine with 14 marketplace plugins
7. **WASM Sandbox** — Extism-based sandboxed execution for untrusted plugins
8. **Per-Model Pricing** — Accurate cost tracking for 30+ models
9. **Active Health Probing** — Background endpoint liveness checks with circuit breakers
10. **Request Deduplication** — X-Idempotency-Key support

![SOC Dashboard](/screenshots/soc-dashboard.png)

## Route Architecture

The `RotatorAgent` orchestrates 9 route modules under `proxy/routes/`:

| Module | Routes | Responsibility |
|--------|--------|----------------|
| `chat.py` | `/v1/chat/completions` | Core proxy with auth, identity, RBAC, budget |
| `completions.py` | `/v1/completions` | Legacy text completion endpoint |
| `embeddings.py` | `/v1/embeddings` | Embedding endpoint with PII check |
| `models.py` | `/v1/models` | OpenAI-compatible model discovery |
| `admin.py` | `/api/v1/proxy/*` | Proxy control, features, analytics, audit |
| `registry.py` | `/api/v1/registry/*` | Endpoint CRUD, SSE telemetry |
| `identity.py` | `/api/v1/identity/*` | SSO config, token exchange |
| `plugins.py` | `/api/v1/plugins/*` | Plugin lifecycle management |
| `telemetry.py` | `/health`, `/metrics` | Health probes, Prometheus metrics |

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.12+, FastAPI, uvicorn, aiohttp |
| Frontend | Vanilla JS (ES Modules), Tailwind CSS, Chart.js, xterm.js |
| Database | SQLite (aiosqlite) |
| Observability | OpenTelemetry, Prometheus, Sentry |
| Security | PyJWT, OIDC/JWKS, mTLS, Tailscale Zero-Trust |
| Secrets | Infisical SDK + env fallback |

## Why LLMProxy?

- **Security-first**: Every request passes through injection detection, PII masking, and trajectory analysis before reaching any LLM
- **Provider-agnostic**: Single API endpoint supporting 15 providers with automatic format translation
- **Cost control**: Per-model pricing, budget enforcement, automatic model downgrading for simple prompts
- **Observable**: Prometheus metrics, OpenTelemetry traces, real-time SOC dashboard, webhook alerts
- **Extensible**: Ring-based plugin pipeline with Python SDK and WASM sandbox for untrusted code

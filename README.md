# LLMProxy

Security gateway for Large Language Models. Routes requests across 15 providers with automatic fallback, cost-aware smart routing, and a 6-layer defense pipeline. Drop-in replacement for the OpenAI API.

![Python](https://img.shields.io/badge/python-3.12%2B-blue?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.110%2B-009688?logo=fastapi&logoColor=white)
![Tests](https://img.shields.io/badge/tests-915%20passing-brightgreen)
![Coverage](https://img.shields.io/badge/coverage-67%25-yellowgreen)
![License: MIT](https://img.shields.io/badge/license-MIT-green)
[![CI](https://github.com/fabriziosalmi/llmproxy/actions/workflows/ci.yml/badge.svg)](https://github.com/fabriziosalmi/llmproxy/actions/workflows/ci.yml)

![LLMProxy Dashboard](screenshot.png)

---

## Why LLMProxy

- **One endpoint, 15 providers** -- Send OpenAI-compatible requests and let the proxy handle translation, failover, and cost optimization across OpenAI, Anthropic, Google, Azure, Ollama, Groq, Together, Mistral, DeepSeek, xAI, Perplexity, Fireworks, OpenRouter, and SambaNova.
- **Security by default** -- Byte-level ASGI firewall, injection scoring, PII masking, cross-session threat intelligence, immutable audit ledger, HMAC response signing. Fail-closed auth middleware denies all admin paths unless explicitly whitelisted.
- **Cost control** -- Per-model pricing for 30+ models, daily budget limits with automatic downgrade to local models, per-session spend tracking, cost-efficiency analytics.
- **Extensible** -- 18 marketplace plugins (budget guard, A/B routing, schema enforcement, canary detection, ...) with a ring-based pipeline. Write your own in Python or WASM.

---

## Quick Start

```bash
git clone https://github.com/fabriziosalmi/llmproxy && cd llmproxy
make setup                          # Install deps, create .env
# Edit .env -- set LLM_PROXY_API_KEYS and at least one provider key
make run                            # Start on port 8090

curl http://localhost:8090/health
curl http://localhost:8090/v1/chat/completions \
  -H "Authorization: Bearer $YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model": "gpt-4o", "messages": [{"role": "user", "content": "Hello"}]}'
```

Or with Docker:

```bash
cp .env.example .env                # Edit with your API keys
docker compose up -d
```

[![Open in GitHub Codespaces](https://github.com/codespaces/badge.svg)](https://codespaces.new/fabriziosalmi/llmproxy)

---

## Architecture

```
Client Request
  |
  +-- RateLimitMiddleware         Token bucket per IP/key (O(1) LRU, 50k max)
  +-- ByteLevelFirewall           28 signatures, 8 encoding layers, iterative chain decoding
  +-- CORSMiddleware
  +-- Global Auth (fail-closed)   Deny-all for /api/v1/*, /admin/*, /metrics
  +-- SecurityShield              Injection scoring, PII masking, trajectory analysis
  |     +-- ThreatLedger          Cross-session IP + key aggregation
  |     +-- SemanticAnalyzer      64 patterns, 12 languages, leetspeak normalization
  |
  +-- Ring 1: INGRESS             Auth, Zero-Trust, rate limiting
  +-- Ring 2: PRE-FLIGHT          PII masking, budget guard, cache, complexity scoring
  +-- Ring 3: ROUTING             Model selection, load balancing, A/B routing
  +-- Upstream Provider           Automatic format translation + fallback chain
  +-- Ring 4: POST-FLIGHT         Response sanitization, quality gate, schema enforcement
  +-- Ring 5: BACKGROUND          Telemetry, export, shadow traffic
  |
Client Response
```

### Providers

OpenAI, Anthropic, Google (Gemini), Azure OpenAI, Ollama, Groq, Together, Mistral, DeepSeek, xAI (Grok), Perplexity, Fireworks, OpenRouter, SambaNova. Each with a dedicated adapter that handles request/response format translation, streaming, and error mapping.

### Smart Routing

Endpoints are scored using an EMA-weighted formula: `score = (success^2 / latency) * cost_factor^w`. The proxy automatically routes to the best-scoring endpoint, with configurable fallback chains (e.g., GPT-4o fails -> Claude Sonnet -> Gemini Pro). When daily budget is exhausted, requests are auto-downgraded to a local model (Ollama).

---

## Security

| Layer | What it does |
|-------|-------------|
| **ASGI Firewall** | 28 injection signatures across 8 encoding layers (URL, Unicode, Base64, hex, ROT13) with iterative chain decoding. Blocks before JSON parsing. |
| **SecurityShield** | Threat scoring (8 regex patterns, threshold 0.7), multi-turn trajectory detection, cross-session ThreatLedger. |
| **Semantic Analyzer** | 64-pattern trigram Jaccard corpus across 12 languages. Leetspeak normalization, Cyrillic/Greek confusable mapping. Bounded executor with 5s timeout. |
| **PII Detection** | Dual-mode: Presidio NLP (18 entity types) or regex fallback (email, phone, SSN, credit card, IBAN, IP, API keys). Vault-based mask/demask roundtrip. |
| **Response Sanitization** | Entropy guard, steganography detection (bidi overrides, zero-width chars, homoglyphs), prompt leak detection. |
| **Audit Ledger** | SHA256 hash-chained audit log with tamper detection. GDPR compliance: right to erasure, DSAR export, configurable retention. |

Auth: API keys, OIDC/JWT (Google, Microsoft, Apple), mTLS, Tailscale Zero-Trust. RBAC with four roles (admin, operator, user, viewer).

HMAC-SHA256 response signing proves the response was not modified after leaving the proxy.

See [SECURITY.md](SECURITY.md) for the full security architecture and vulnerability disclosure policy.

---

## API

LLMProxy exposes an OpenAI-compatible API on port 8090.

### Inference

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/v1/chat/completions` | `POST` | Chat completion (streaming + non-streaming). 15 providers. |
| `/v1/completions` | `POST` | Legacy text completion. |
| `/v1/embeddings` | `POST` | Embeddings (OpenAI, Google, Ollama, Azure). |
| `/v1/models` | `GET` | Model discovery (aggregated from all providers). |
| `/health` | `GET` | Liveness probe. |
| `/metrics` | `GET` | Prometheus metrics. |

### Administration

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v1/registry` | `GET` | Endpoint pool state. |
| `/api/v1/registry/{id}/toggle` | `POST` | Enable/disable an endpoint. |
| `/api/v1/proxy/toggle` | `POST` | Enable/disable the proxy. |
| `/api/v1/panic` | `POST` | Emergency kill switch. |
| `/api/v1/features` | `GET` | Security guard feature flags. |
| `/api/v1/features/toggle` | `POST` | Toggle a guard. |
| `/api/v1/analytics/spend` | `GET` | Spend breakdown by model/provider/key/date. |
| `/api/v1/audit` | `GET` | Audit log query with filters. |
| `/api/v1/audit/verify` | `GET` | Verify audit chain integrity. |
| `/api/v1/plugins` | `GET` | List installed plugins. |
| `/api/v1/plugins/install` | `POST` | Install a plugin (AST-scanned, hot-swapped). |
| `/api/v1/gdpr/erase/{subject}` | `POST` | Right to erasure (Article 17). |
| `/api/v1/gdpr/export/{subject}` | `GET` | Data subject access request (Article 15). |

Full API reference in the [docs](docs/).

---

## Plugins

Ring-based pipeline with 18 marketplace plugins and 10 built-in defaults.

| Plugin | Ring | Description |
|--------|------|-------------|
| Smart Budget Guard | Pre-Flight | Per-session/team budget with SQLite persistence. |
| Agentic Loop Breaker | Pre-Flight | Detects AI agents stuck in retry loops. |
| Model Downgrader | Pre-Flight | Auto-downgrades expensive models for simple prompts. |
| Context Window Guard | Pre-Flight | Blocks requests exceeding model context limit. |
| Topic Blocklist | Pre-Flight | Keyword/regex topic filtering. |
| Tool Guard | Pre-Flight | Strips restricted tools from agentic requests. |
| A/B Model Router | Routing | Routes traffic percentage to variant model. |
| Tenant QoS Router | Routing | Routes by tenant tier (free/basic/premium). |
| Response Quality Gate | Post-Flight | Detects empty, refused, or truncated responses. |
| Canary Detector | Post-Flight | Detects system prompt leakage. |
| Schema Enforcer | Post-Flight | Validates JSON responses against schema. |
| Shadow Traffic | Background | Dark-launch to shadow model for comparison. |

Write your own:

```python
from core.plugin_sdk import BasePlugin, PluginResponse, PluginHook

class MyPlugin(BasePlugin):
    name = "my_plugin"
    hook = PluginHook.PRE_FLIGHT
    version = "1.0.0"

    async def execute(self, ctx):
        return PluginResponse.passthrough()
```

WASM plugins (Rust/Go/C) are supported via Extism for untrusted code execution. See [plugins/](plugins/) for the full development guide.

---

## Configuration

```yaml
server:
  host: 0.0.0.0
  port: 8090
  auth: { enabled: true, api_keys_env: "LLM_PROXY_API_KEYS" }

endpoints:
  openai:
    provider: "openai"
    base_url: "https://api.openai.com/v1"
    api_key_env: "OPENAI_API_KEY"
    models: ["gpt-4o", "gpt-4o-mini"]
  anthropic:
    provider: "anthropic"
    base_url: "https://api.anthropic.com/v1"
    api_key_env: "ANTHROPIC_API_KEY"
    models: ["claude-sonnet-4-20250514"]

fallback_chains:
  "gpt-4o":
    - { provider: anthropic, model: "claude-sonnet-4-20250514" }
    - { provider: google, model: "gemini-2.5-pro" }

budget:
  daily_limit: 50.0
  fallback_to_local_on_limit: true

rate_limiting:
  enabled: true
  requests_per_minute: 60
```

All secrets are loaded from environment variables (Infisical SDK supported). See [config.yaml](config.yaml) for the full reference.

---

## Frontend

Real-time Security Operations Center UI at `/ui`.

| View | What it shows |
|------|--------------|
| Threats | KPI cards, threat timeline chart, ring latency (P50/P95/P99), live SSE event feed |
| Guards | Master proxy toggle, per-guard enable/disable with descriptions |
| Plugins | Pipeline grid with per-plugin stats, install/uninstall/hot-swap |
| Models | Aggregated model registry with search/filter |
| Analytics | Spend breakdown by model and provider |
| Security | Audit chain verification, GDPR controls, semantic corpus stats |
| Endpoints | Registry table with circuit breaker state, priority, toggle/delete |
| Live Logs | xterm.js terminal with WebGL rendering and JSON syntax highlighting |
| Settings | Identity, RBAC matrix, webhooks, data export |

Keyboard shortcuts: `Cmd+K` (command palette), `F` (cinema mode). URL hash routing (`#/guards`, `#/logs`, ...).

---

## Observability

- **Prometheus** -- 10 metrics (requests, errors, latency percentiles, TTFT, tokens, cost, budget, circuit state, injection blocks, auth failures). Pre-built Grafana dashboard and alert rules in `monitoring/`.
- **OpenTelemetry** -- Distributed tracing via OTLP. Graceful degradation when not installed.
- **Sentry** -- Exception tracking with PII filtering and sampling.
- **Webhooks** -- Slack, Teams, Discord, Generic (JSON). HMAC-SHA256 signed. SSRF-protected.
- **Dataset Export** -- Async JSONL with PII scrubbing, gzip rotation, optional Parquet conversion.

---

## Testing

```bash
make test       # 915 tests, ~19s
make bench      # 22 performance benchmarks
make lint       # ruff
make typecheck  # mypy
```

915 tests across 46 modules: unit, HTTP integration, pipeline E2E, property-based fuzz (Hypothesis), 31 mathematical invariant proofs, concurrency stress tests, and performance benchmarks.

The invariant suite proves correctness properties (Jaccard axioms, normalize idempotence, token conservation, budget accounting, adapter determinism) and blocks merge on violation.

---

## Production Checklist

| Setting | Default | Production |
|---------|---------|------------|
| TLS | Disabled | Enable or use a reverse proxy (Traefik, Caddy, nginx) |
| CORS | `["*"]` | Restrict to your frontend origin(s) |
| Auth | Enabled | Keep enabled, rotate API keys |
| API keys | Placeholder | Replace with strong keys |
| Presidio | Not installed | `pip install presidio-analyzer presidio-anonymizer` for NLP PII |
| tiktoken | Not installed | `pip install tiktoken` for accurate token counting |

The proxy logs warnings at startup when TLS is disabled or CORS is unrestricted.

For hardened deployments, pair with [secure-proxy-manager](https://github.com/fabriziosalmi/secure-proxy-manager) for network-level egress filtering (domain whitelisting, direct IP blocking, IMDS protection).

---

## CI/CD

GitHub Actions runs 8 jobs on every push: lint (ruff), type check (mypy), dependency audit (pip-audit), supply chain scan (`.pth` malware + blocked packages), syntax check, test suite with coverage gate (65%), mathematical invariants, and Docker image size check.

---

## License

MIT. See [LICENSE](LICENSE).

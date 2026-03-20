# LLMProxy — Universal AI Gateway & Autonomous Agentic Mesh

Professional high-performance aggregator, intelligent load balancer, and autonomous discovery engine for Large Language Models. LLMProxy provides a unified, hardened interface for pluralistic AI environments with advanced security, zero-latency observability, and self-healing agent swarms.

## Table of Contents
1. [Architecture Overview](#architecture-overview)
2. [Core Components](#core-components)
3. [API Reference](#api-reference)
4. [Autonomous Agent Swarm](#autonomous-agent-swarm)
5. [Security & Identity](#security--identity)
6. [Plugin Engine](#plugin-engine)
7. [Semantic Caching](#semantic-caching)
8. [Observability & Export](#observability--export)
9. [ChatOps & Webhooks](#chatops--webhooks)
10. [Frontend HUD](#frontend-hud)
11. [Configuration](#configuration)
12. [Testing](#testing)
13. [Installation & Deployment](#installation--deployment)
14. [Advanced Features](#advanced-features)

---

## Architecture Overview

LLMProxy is engineered as a multi-tier, distributed system. It separates high-speed request processing (Edge Tier) from background autonomous operations (Agentic Tier), ensuring that system intelligence does not introduce latency into the critical path of inference.

### System Tiers
- **Edge Tier (L7)**: ASGI-based request pipeline, OIDC/JWT authentication, Byte-Level Firewall, and Ring-based plugin pipeline.
- **Agentic Tier (L2/L3)**: Supervisor-managed swarm for discovery, validation, and self-healing.
- **Persistence Tier**: Multi-modal storage (SQLite for metadata/RBAC, ChromaDB for semantic patterns).

### Tech Stack
| Layer | Technology |
|-------|-----------|
| Backend | Python 3.12+, FastAPI, uvicorn, aiohttp |
| Frontend | Vanilla JS (ES Modules), Tailwind CSS, Chart.js, xterm.js |
| Database | SQLite (aiosqlite) + ChromaDB (vector search) |
| Observability | OpenTelemetry, Prometheus, Sentry |
| Security | PyJWT, OIDC/JWKS, mTLS, Tailscale Zero-Trust |
| Secrets | Infisical SDK + env fallback |

---

## Core Components

### 1. Agent Supervisor
The heart of LLMProxy's background operations. Manages a DAG (Directed Acyclic Graph) of agents with exponential backoff restart and localized circuit breaking.

### 2. SOTA Interface Agent (10x Discovery)
Playwright-based autonomous discovery:
- **Network Traffic Sniffing**: Intercepts fetch/XHR calls to discover hidden API endpoints.
- **Genetic Evasion**: Non-linear mouse movements and micro-scroll jitter to bypass WAFs.
- **Pattern Prediction**: Vector search (`PatternMemory`) predicts the correct API adapter based on structural similarity.

### 3. Unified Adapter Engine
Translates OpenAI-compatible requests into proprietary provider formats (Anthropic, HuggingFace, Local LLMs) in real-time, handling prompt templating and multi-modal payload mapping.

### 4. Semantic Router & RL Rotator
- **Complexity-Aware Routing**: Analyzes prompt complexity to route to the optimal model.
- **Reinforcement Learning**: `RLRotator` learns from latency/cost/quality signals to optimize endpoint selection over time.

### 5. Circuit Breaker & Federation
- **Per-endpoint circuit breakers** with configurable thresholds.
- **Federated Mesh**: Peer-to-peer request offloading between LLMProxy instances via Tailscale, with trust secret validation.

---

## API Reference

### Model Proxy (Port 8090)
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/v1/chat/completions` | `POST` | Unified inference endpoint (OpenAI-compatible). Supports model selection, JWT/API key auth. |
| `/health` | `GET` | Liveness/readiness probe with pool stats. |
| `/metrics` | `GET` | Prometheus metrics: req/s, errors, latency P50/P95/P99, budget, TTFT, circuit state. |

### Registry & Control
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v1/registry` | `GET` | Full model pool state (Live/Discovered/Offline). |
| `/api/v1/registry/{id}/toggle` | `POST` | Toggle endpoint on/off. |
| `/api/v1/registry/{id}/priority` | `POST` | Set endpoint routing priority. |
| `/api/v1/registry/{id}` | `DELETE` | Remove an endpoint. |
| `/api/v1/proxy/toggle` | `POST` | Enable/disable the proxy. |
| `/api/v1/proxy/status` | `GET` | Proxy enabled state + priority mode. |
| `/api/v1/proxy/priority/toggle` | `POST` | Toggle priority steering mode. |

### Identity & SSO
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v1/identity/me` | `GET` | Current user identity, roles, and permissions (from JWT or API key). |
| `/api/v1/identity/exchange` | `POST` | Exchange external OIDC JWT for internal proxy session token. |
| `/api/v1/identity/config` | `GET` | Public SSO provider list (for frontend OAuth flow). |

### Plugins
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v1/plugins` | `GET` | List all plugins with marketplace metadata (version, author, ui_schema). |
| `/api/v1/plugins/install` | `POST` | Install a plugin (AST-scanned, then hot-swapped). |
| `/api/v1/plugins/{name}` | `DELETE` | Uninstall a plugin. |
| `/api/v1/plugins/toggle` | `POST` | Enable/disable a plugin. |
| `/api/v1/plugins/hot-swap` | `POST` | Zero-downtime RCU reload with health check. |
| `/api/v1/plugins/rollback` | `POST` | Rollback to previous plugin state. |

### System
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v1/features` | `GET` | Feature flags (language_guard, injection_guard, link_sanitizer). |
| `/api/v1/features/toggle` | `POST` | Toggle a security feature. |
| `/api/v1/telemetry/stream` | `GET` | Real-time SSE stream of system events. |
| `/api/v1/logs` | `GET` | SSE log stream for terminal. |
| `/api/v1/panic` | `POST` | Emergency kill-switch — halts all traffic. |
| `/api/v1/version` | `GET` | Current version. |
| `/api/v1/service-info` | `GET` | Host, port, URL. |
| `/api/v1/network/info` | `GET` | Network and Tailscale status. |

---

## Autonomous Agent Swarm

The swarm utilizes a Finite State Machine (FSM) for predictable transitions and robust error handling.

| Agent | Module | Primary Intelligence |
|-------|--------|----------------------|
| **SOTA Interface** | `agents/sota_interface_agent.py` | Playwright-based API synthesis and WAF evasion. |
| **Scanner** | `agents/scanner.py` | BFS traversal of web targets to identify potential LLM resources. |
| **Validator** | `agents/validator.py` | Ground-truth verification of model logic and alignment. |
| **Self-Healer** | `agents/self_healer.py` | Auto-remediation of registry drift and service degradation. |
| **Distiller** | `agents/distiller.py` | High-fidelity dataset extraction for SLM fine-tuning. |

---

## Security & Identity

### Byte-Level Firewall (Speculative Guardrails)
Zero-latency ASGI middleware scanning raw byte streams:
- **Pattern matching** for injection signatures (`ignore previous instructions`, `bypass guardrails`).
- **Instant termination**: Force-closes socket mid-stream on violation, preventing remote cost incurrence.
- **Fail-closed**: Returns `False` on any error — never permits traffic when in doubt.

### Identity & SSO (`core/identity.py`)
Stateless multi-provider OIDC/JWT verification:
- **Providers**: Google, Microsoft, Apple — auto-configured via well-known OIDC discovery.
- **JWKS caching**: Keys cached with 1hr TTL, auto-refresh on rotation.
- **Auth flow**: External OIDC JWT → verify via JWKS → exchange for internal proxy JWT → attach `IdentityContext` to request.
- **Fallback chain**: JWT → API key → Tailscale identity (via LocalAPI socket).
- **No user database**: Identity is derived entirely from cryptographic token claims.

### OAuth Frontend (`ui/services/auth.js`)
Browser-based SSO login flow:
- **Popup-based OAuth**: Click provider button → popup opens OIDC authorize URL → `id_token` extracted from URL fragment.
- **Callback handler**: `oauth-callback.html` relays token via `postMessage` to opener window.
- **Token exchange**: `POST /api/v1/identity/exchange` converts external JWT to internal proxy session token (stored in `localStorage`).
- **Guard route**: If identity is enabled and no valid session exists, a glassmorphism login overlay is shown.
- **API key fallback**: Manual API key entry for environments without SSO.

### RBAC (`core/rbac.py`)
Four built-in roles with granular permissions:

| Role | Key Permissions |
|------|----------------|
| `admin` | Full access: proxy, registry, chat, logs, plugins, users, budget. |
| `operator` | Proxy toggle, registry write, plugins manage, features toggle. |
| `user` | Proxy use, registry read, chat, logs read. |
| `viewer` | Registry read, logs read only. |

- JWT claims → role mapping via `config.yaml` (`role_mappings`).
- Roles persisted in SQLite `user_roles` table.
- Budget quota enforcement per API key.

### Zero-Trust & mTLS
- **Tailscale LocalAPI**: Verifies machine/user identity via Unix socket (`whois` API).
- **mTLS Pipeline**: Client certificate verification for upstream provider connections.
- **URL injection prevention**: All user-supplied IPs/URLs are escaped via `urllib.parse.quote()`.

---

## Plugin Engine

**Dual-mode** ring-based architecture (`core/plugin_engine.py`) with 5 processing stages. Supports both legacy raw functions and `BasePlugin` class instances side by side — zero breaking changes.

| Ring | Stage | Purpose |
|------|-------|---------|
| 1 | **Ingress** | Auth, Zero-Trust, Global Rate Limiting |
| 2 | **Pre-Flight** | PII Masking, Prompt Mutation, Budget Guard, Loop Breaker |
| 3 | **Routing** | Semantic Cache Lookup, Dynamic Model Selection |
| 4 | **Post-Flight** | JSON Healing, Response Sanitization, Watermarking |
| 5 | **Background** | FinOps Tracking, Telemetry Export, Shadow Traffic |

### Plugin SDK (`core/plugin_sdk.py`)
The official SDK for building marketplace plugins:

```python
from core.plugin_sdk import BasePlugin, PluginResponse, PluginHook
from core.plugin_engine import PluginContext

class MyPlugin(BasePlugin):
    name = "my_plugin"
    hook = PluginHook.PRE_FLIGHT
    version = "1.0.0"
    timeout_ms = 50  # Strict timeout enforcement

    async def execute(self, ctx: PluginContext) -> PluginResponse:
        # Your logic here
        return PluginResponse.passthrough()  # or .block(), .modify(), .cache_hit()
```

**PluginResponse** typed contracts:
| Action | Effect |
|--------|--------|
| `passthrough` | Let the request continue unchanged |
| `modify` | Mutate request body, continue pipeline |
| `block` | Stop chain, return error to client (status code + error type) |
| `cache_hit` | Return cached response, skip routing |

### Dual-Mode Execution
- **Class plugins** (`BasePlugin` subclasses): `execute(ctx) → PluginResponse`, with `on_load()` / `on_unload()` lifecycle hooks.
- **Function plugins** (legacy): raw `async def func(ctx)` — existing plugins work unchanged.
- **Auto-detection**: the engine inspects the entrypoint — if it's a class subclassing `BasePlugin`, it instantiates it; otherwise treats it as a raw function.

### Strict Timeout Enforcement
Every plugin runs under `asyncio.wait_for(timeout)`:
- Configurable per-plugin via `timeout_ms` in manifest (default 5000ms for functions, custom per class plugin).
- Timeout kills the coroutine and logs a warning.
- Ingress/Routing timeouts are fatal (stop chain).

### Per-Plugin Metrics
Tracked automatically by the engine:
- `invocations`, `errors`, `blocks`, `timeouts`, `total_latency_ms`, `avg_latency_ms`.
- Queryable via `PluginManager.get_plugin_stats(name)` or `get_plugin_stats()` for all.

### Marketplace Plugins (Built-in)

| Plugin | Ring | Description |
|--------|------|-------------|
| **Agentic Loop Breaker** | Pre-Flight | Detects AI agents stuck in retry loops via SHA-256 prompt hashing with sliding window. Blocks with 429 + diagnostic message. |
| **Smart Budget Guard** | Pre-Flight | Pre-flight cost estimation with per-session and per-team budget enforcement. Warning threshold + post-flight actual cost correction. |

Both ship with `ui_schema` for dynamic UI rendering and configurable parameters.

### Default Plugins (Legacy Functions)

9 built-in function plugins (backward compatible, no changes needed):
- Ingress Auth & Zero-Trust, PII Neural Masker, Semantic Cache Hook, Enterprise Neural Router, Post-Flight Sanitizer, Unified Telemetry & FinOps, Context Minifier, Kill-Switch, JSON Auto-Healer.

### Security Scanning
All Python plugins are **AST-scanned** before loading:
- Blocks: `os`, `subprocess`, `socket`, `ctypes`, `sys` imports.
- Blocks: `exec()`, `eval()`, `__import__()`, `.system()`, `.popen()` calls.
- Raises `PluginSecurityError` on violation — plugin is never loaded.

### Zero-Downtime Hot-Swap (RCU)
1. Call `on_unload()` on all existing `BasePlugin` instances.
2. Snapshot current ring state (rollback target).
3. Load new plugin configuration into fresh rings.
4. Call `on_load()` on new `BasePlugin` instances.
5. Health check: run dummy context through all rings.
6. Atomic swap: replace active rings reference.
7. Auto-rollback on any failure.

### WASM Plugin Support (`core/wasm_runner.py`)
Execute Rust/Go/C plugins compiled to WebAssembly via Extism SDK:
- **Memory-safe sandboxing**: WASM plugins run in an isolated VM — crashes cannot affect the Python process.
- **Non-blocking execution**: All WASM calls run via `asyncio.to_thread()`, releasing the GIL and keeping the event loop free.
- **JSON I/O protocol**: Input (`body`, `metadata`, `session_id`, `config`) → Output (`action`, `body`, `status_code`, `message`). Aligned with `PluginResponse` contracts.
- **Legacy compat**: Maps WASM actions (`ALLOW`/`BLOCK`/`MODIFIED`) to standard actions (`passthrough`/`block`/`modify`).
- **Graceful fallback**: If Extism is not installed, WASM plugins are skipped silently (no crash).
- **Same guarantees**: Timeout enforcement, per-plugin metrics, and fail_policy apply to WASM plugins identically to Python plugins.

See [`plugins/wasm/README.md`](plugins/wasm/README.md) for a complete Rust plugin development guide (Cargo.toml, lib.rs template, build instructions).

### Marketplace API
Install, uninstall, toggle, hot-swap, and rollback plugins via REST API. Plugin manifests support `ui_schema` for dynamic UI rendering, versioning, author metadata, and per-plugin `config` blocks.

---

## Semantic Caching

### 1-Bit Vector Quantization
The `SemanticCache` binarizes embeddings into 1-bit vectors, rendering Cosine Similarity mathematically equivalent to **XOR Hamming Distance** for CPU-level hardware acceleration.

### Deterministic Bloom Filter
Front-end Bloom Filter (`bloom.bin`) prevents expensive vector DB lookups for guaranteed misses — O(1) latency for unique queries.

---

## Observability & Export

### Prometheus Metrics (`/metrics`)
| Metric | Type | Description |
|--------|------|-------------|
| `llm_proxy_requests_total` | Counter | Total requests by method, endpoint, status. |
| `llm_proxy_request_errors_total` | Counter | Failed requests (4xx/5xx) by error class. |
| `llm_proxy_request_latency_seconds` | Histogram | Latency with P50/P95/P99 buckets (10ms → 60s). |
| `llm_proxy_streaming_ttft_seconds` | Histogram | Time To First Token for streaming responses. |
| `llm_proxy_token_usage_total` | Counter | Token usage by endpoint and role (prompt/completion). |
| `llm_proxy_cost_total` | Counter | Estimated cost in USD by endpoint and model. |
| `llm_proxy_budget_consumed_usd` | Gauge | Current month budget consumption. |
| `llm_proxy_circuit_open` | Gauge | Circuit breaker state per endpoint. |
| `llm_proxy_injection_blocked_total` | Counter | Injection attempts blocked. |
| `llm_proxy_auth_failures_total` | Counter | Authentication failures by reason. |

### OpenTelemetry
- **Traces**: Distributed tracing via OTLP with optional console exporter.
- **Resource tags**: `service.name` for multi-instance identification.
- **FastAPI auto-instrumentation**: All routes traced automatically.

### Sentry Integration
- Exception tracking with FastAPI + aiohttp integrations.
- PII filtering (`send_default_pii=False`).
- Event sampling (10% transactions, 5% profiles).
- HTTPException events dropped to reduce noise.

### Dataset Export (`core/export.py`)
- **Async JSONL writer** with daily file rotation.
- **PII scrubbing**: Emails, IPs, API keys, JWTs automatically redacted.
- **Gzip compression** on rotation.
- **Optional Parquet conversion** via pyarrow with zstd compression.

### SQLite Replication
Litestream configuration documented in `config.yaml` for WAL-mode SQLite → S3 continuous replication (10s sync interval, 30-day retention).

---

## ChatOps & Webhooks

### Webhook Dispatcher (`core/webhooks.py`)
Generic HTTP POST dispatcher with platform-specific formatters:
- **Slack**: Block Kit (`mrkdwn` sections).
- **Microsoft Teams**: MessageCard with theme colors.
- **Discord**: Embeds with severity-colored sidebars.
- **Generic**: JSON payload with event metadata.

7 event types: `circuit_open`, `budget_threshold`, `injection_blocked`, `endpoint_down`, `endpoint_recovered`, `auth_failure`, `panic_activated`.

30-second debounce prevents webhook flooding for identical events.

### Telegram Bot (`core/chatops.py`)
Async long-polling bot (no webhook server required):

| Command | Description |
|---------|-------------|
| `/status` | Proxy status and pool health. |
| `/budget` | Budget consumption overview. |
| `/approve <id>` | Approve a pending HITL request. |
| `/reject <id>` | Reject a pending HITL request. |

### Human-in-the-Loop (HITL)
For "soft-violation" requests:
1. SecurityShield flags the request.
2. Request enters hold queue (5-minute timeout).
3. Telegram notification sent to ops channel.
4. Operator replies `/approve` or `/reject`.
5. Request continues or is rejected.

### Auto-Ticketing
Debounced error rate alerting: >50 errors/hour triggers a summary notification with 10-minute cooldown.

---

## Frontend HUD

Vanilla JS single-page application (`ui/`) with ES Modules, Tailwind CSS CDN, and a glassmorphism dark theme.

### Design System
- **Dual-Typeface**: Inter (UI) + JetBrains Mono (data/mono).
- **Glassmorphism**: `backdrop-filter: blur(20px) saturate(180%)` on all panels.
- **Grain overlay**: SVG noise at 2.8% opacity, mix-blend-mode overlay.
- **Sub-pixel glow**: Hover-activated box-shadow on cards and topology nodes.
- **Mobile responsive**: Sidebar overlay, adaptive grid (2→3→5 columns), touch-friendly.

### Views

**Dashboard**
- 5 KPI cards with unique sparklines (uptrend/plateau/descending).
- Main chart with gradient fill, X-axis baseline, P99 SLA threshold line.
- Latency color logic with live delta badges.

**Registry**
- Drag-and-drop priority reordering (HTML5 Drag API).
- Inline priority editing with transparent input.
- Health heatmap per endpoint (10-sample history).
- Right-click context menu: Toggle, Copy ID, View Latency, Set Priority, Delete.
- Click provider name → slide-over detail panel (400px) with Chart.js latency graph, health bars, and action buttons.

**Neural Chat**
- Model dropdown selector: Router Auto, GPT-4o, Claude Sonnet 4, Llama 3.3 70B, Gemini 2.5 Pro, DeepSeek R1.
- A/B/C Compare mode: 3-column parallel streaming from different models.
- User/bot bubble differentiation with TTFT/token/cost telemetry on hover.
- Injection guard visualization with redaction animation.
- Token-aware live cost counter.

**Transparent Proxy**
- Unified system toggles with smooth animations.
- xterm.js terminal with WebGL acceleration, Fira Code font.
- Live diff rendering (GitHub-style red/green with ANSI backgrounds).
- Native JSON pretty-print with syntax highlighting (keys, strings, numbers, booleans, null).
- Intelligent autoscroll with "+N hidden" badge.
- Topology map with animated particle flow.

**Operations**
- Password visibility toggle.
- Regenerate key with double-click confirmation.
- Emergency kill-switch with confirm dialog.

### Interactions
- **Command Palette**: `Cmd+K` with fuzzy search.
- **Skeleton shimmer loaders** on view transitions.
- **Copy 1-click** with "COPIED!" feedback.
- **Network heartbeat**: 5s ping, LIVE/OFFLINE indicator.
- **Token Flow equalizer** with animated bars.

---

## Configuration

### `config.yaml` Structure
```yaml
server:
  host: 0.0.0.0
  port: 8090
  tls: { enabled: true, min_version: "1.2" }
  auth: { enabled: true, api_keys_env: "LLM_PROXY_API_KEYS" }

identity:
  enabled: false
  providers:
    - name: google
      client_id_env: "OIDC_GOOGLE_CLIENT_ID"
    - name: microsoft
      client_id_env: "OIDC_MICROSOFT_CLIENT_ID"
  role_mappings: {}
  session_ttl: 3600

rotation:
  strategy: "round_robin"  # weighted, least_used, random
  failover: { enabled: true, max_retries: 3 }

webhooks:
  enabled: false
  endpoints:
    - name: slack-ops
      target: slack
      url_env: "SLACK_WEBHOOK_URL"
      events: ["circuit_open", "budget_threshold", "panic_activated"]

chatops:
  telegram:
    enabled: false
    token_env: "TELEGRAM_BOT_TOKEN"

observability:
  tracing: { enabled: true, service_name: "llmproxy" }
  sentry: { dsn_env: "SENTRY_DSN" }
  export: { enabled: false, output_dir: "exports", scrub_pii: true }

budget:
  monthly_limit: 1000.0
  soft_limit: 800.0
  fallback_to_local_on_limit: true
```

### Secrets Management
All sensitive values are loaded via **Infisical SDK** with environment variable fallback:
- `LLM_PROXY_API_KEYS` — Comma-separated API keys.
- `LLM_PROXY_MASTER_KEY` — Encryption master key (for at-rest secrets).
- `LLM_PROXY_IDENTITY_SECRET` — Internal JWT signing key.
- `LLM_PROXY_FEDERATION_SECRET` — Federation trust secret.
- `OIDC_*_CLIENT_ID` — Per-provider OIDC client IDs.
- `SENTRY_DSN` — Sentry error tracking DSN.
- `TELEGRAM_BOT_TOKEN` — Telegram ChatOps bot token.
- `SLACK_WEBHOOK_URL` / `DISCORD_WEBHOOK_URL` — Webhook URLs.

---

## Testing

93 tests across 9 modules, all passing on `pytest` + `pytest-asyncio`.

```bash
# Run full suite
python -m pytest tests/ -v --ignore=tests/test_store.py --ignore=tests/integrated_test.py

# Run a specific module
python -m pytest tests/test_marketplace_plugins.py -v
```

| Module | Tests | Coverage |
|--------|-------|----------|
| `test_identity.py` | 7 | OIDC verify, proxy JWT gen/verify/expire, role mapping |
| `test_rbac.py` | 7 | Admin/user/viewer permissions, multi-role, quota, user CRUD |
| `test_webhooks.py` | 6 | Slack/Teams/Discord/Generic format, severity mapping |
| `test_chatops.py` | 5 | Telegram polling, HITL approve/reject/timeout, error tracking |
| `test_export.py` | 8 | PII scrub (email/IP/key/bearer), nested redaction, file rotation |
| `test_plugin_engine.py` | 8 | AST scan (safe/forbidden: os/subprocess/exec/eval), allowed modules |
| `test_metrics.py` | 5 | Counter increment, error class, injection blocked, budget, circuit |
| `test_marketplace_plugins.py` | 30 | Loop Breaker (7), Budget Guard (5), Engine dual-mode (5), Fail policy (2), AST blocking (5), Validation (4), DI State (2) |
| `test_wasm_runner.py` | 15 | JSON protocol, legacy action mapping, error handling, engine integration |

---

## Installation & Deployment

### Quick Start
```bash
# Clone
git clone https://github.com/fabriziosalmi/llmproxy
cd llmproxy

# Install dependencies
pip install -r requirements.txt

# Playwright setup (for SOTA Explorer agent)
playwright install chromium

# Copy env template and configure
cp .env.example .env

# Run
python main.py

# UI available at http://localhost:8090/ui
# API available at http://localhost:8090/v1/chat/completions
# Metrics at http://localhost:8090/metrics
```

### Docker Compose
```bash
# Copy env template
cp .env.example .env
# Edit .env with your API keys and secrets

# Start
docker compose up -d

# Logs
docker compose logs -f llmproxy

# Health check
curl http://localhost:8090/health
```

The `docker-compose.yml` includes:
- Health check (30s interval, 3 retries, 15s start period).
- Volume mounts: `llmproxy-data` for persistence, `config.yaml` and `plugins/` read-only.
- Resource limits: 2GB memory limit, 512MB reservation.
- Ports: 8090 (API) + 9091 (Prometheus).

### CI/CD (GitHub Actions)

**`.github/workflows/ci.yml`** — Runs on every push/PR:
- **Lint**: ruff check (Python code quality).
- **Test**: pytest with 93 tests across 9 modules.
- **Syntax**: AST parse of all Python files.

**`.github/workflows/docker.yml`** — Runs on version tags (`v*`):
- Builds Docker image and pushes to GitHub Container Registry (GHCR).
- Tags: semver (`1.0.0`), minor (`1.0`), and commit SHA.

### Optional Dependencies
```bash
# Parquet export
pip install pyarrow

# Sentry integration
pip install sentry-sdk[fastapi]

# WASM plugin support
pip install extism
```

### SQLite Replication (Litestream)
```bash
# Install Litestream
curl -fsSL https://github.com/benbjohnson/litestream/releases/latest -o litestream

# Replicate to S3
litestream replicate -config /etc/litestream.yml

# Restore from S3
litestream restore -o /data/endpoints.db s3://llmproxy-backups/sqlite/endpoints
```

---

## Advanced Features

### Federated Mesh (Tailscale)
Leverages Tailscale mesh networks to discover and shard requests across idle GPU neighbors, creating a private, federated "AI Supercluster" without exposing endpoints to the public internet. Federation trust is verified via shared secret + Tailscale LocalAPI identity.

### MCP (Model Context Protocol) Hub
Unified execution environment for MCP tools, allowing any connected model to leverage a shared pool of local tools (PostgreSQL, Filesystem, Search) via a standardized protocol.

### Visibility & Cloaking
Automatic User-Agent rotation and proxy-chaining to prevent upstream providers from fingerprinting the proxy infrastructure. Supports uTLS JA3 fingerprint forging (Chrome/Safari/Firefox profiles).

### Local LLM Fallback
Automatic failover to local models (via LM Studio / Ollama) when:
- All remote endpoints are down (circuit breakers open).
- Budget limit reached (`fallback_to_local_on_limit: true`).
- Pool success rate falls below threshold.

---

## License

See [LICENSE](LICENSE) for details.

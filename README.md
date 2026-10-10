# LLMProxy

A self-hosted gateway that sits between your applications and LLM providers and
keeps a record of the traffic that you can verify. It speaks the OpenAI API, so
existing clients point at it unchanged. One process, MIT licensed; prompts,
responses and the audit log stay on your infrastructure.

![Python](https://img.shields.io/badge/python-3.12-blue?logo=python&logoColor=white)
![License: MIT](https://img.shields.io/badge/license-MIT-green)
[![CI](https://github.com/fabriziosalmi/llmproxy/actions/workflows/ci.yml/badge.svg)](https://github.com/fabriziosalmi/llmproxy/actions/workflows/ci.yml)

## What it does

- **A verifiable audit log.** Each request that reaches the pipeline, served, refused
  or failed, is a row in a hash chain. The chain can be keyed (HMAC), its head can be
  recorded outside the database, and retention and GDPR erasure leave a record in the
  chain instead of breaking it. [What it proves and what it does not](docs/threat_model.md).
- **Controls on what a response may do.** A [tool policy](docs/security/tool-policy.md)
  decides which tools a response may call and when; PII masking and response
  sanitisation are applied in the request path.
- **Screening of requests.** A byte-level firewall and a scoring shield refuse known
  attack phrasings. They are lexical, and their reach is
  [measured](#what-the-detection-layer-stops-measured), not assumed.
- **Provider translation and fallback.** OpenAI, Anthropic, Google, Azure OpenAI and
  Ollama have their own adapters; 18 further providers are reached through an
  OpenAI-compatible adapter. Fallback chains, a circuit breaker per endpoint, and a
  daily budget limit.

## When not to use it

LLMProxy fits a small or regulated team that self-hosts and has to account for its
LLM traffic. It is the wrong tool when:

- **You need more than one instance.** State such as the daily budget and
  per-session scoring is held in the process. Do not run replicas
  (see [Performance](#performance)).
- **You need high throughput.** About 1.2k requests/s per process; gateways written
  in Go or Rust are built for far more.
- **You need an MCP or agent gateway.** There is none here.
  [agentgateway](https://github.com/agentgateway/agentgateway) covers that ground.
- **You want a hosted service**, or the widest provider coverage (LiteLLM, OpenRouter).

## Quick start

```bash
docker run -d --name llmproxy -p 8090:8090 \
  -e LLM_PROXY_API_KEYS=sk-proxy-change-me \
  -e LLM_PROXY_ADMIN_KEYS=sk-admin-change-me \
  -e OPENAI_API_KEY=$OPENAI_API_KEY \
  -v llmproxy-data:/app/data \
  ghcr.io/fabriziosalmi/llmproxy:1.39.2
```

- `LLM_PROXY_API_KEYS` is required: the shipped configuration authenticates every
  route and the process exits without it. These keys reach `/v1/*`.
- `LLM_PROXY_ADMIN_KEYS` is the control plane (`/api/v1/*`, `/admin/*`). **If it is
  unset, every inference key is also an admin key**; the proxy warns at start.
- The image is built for `linux/amd64`. On Apple Silicon or another ARM host add
  `--platform linux/amd64`.
- Images are tagged `:X.Y.Z`, `:X.Y`, `:latest` and by commit. Pin a release.

Send a request:

```bash
curl http://localhost:8090/v1/chat/completions \
  -H "Authorization: Bearer sk-proxy-change-me" \
  -H "Content-Type: application/json" \
  -d '{"model": "gpt-4o", "messages": [{"role": "user", "content": "Hello"}]}'
```

The admin UI is at `http://localhost:8090/ui`. Providers are declared in
`config.yaml`, in the UI, or in the environment
(`LLM_PROXY_ENDPOINT_<NAME>_URL`, `_KEY`, `_MODELS`; see [.env.example](.env.example)).
The shipped configuration also registers a local Ollama endpoint at
`localhost:11434`.

To run from source: `git clone`, then `./install.sh` (Docker Compose v2 or a local
Python 3.12 virtualenv). The installer creates an inference key only; set
`LLM_PROXY_ADMIN_KEYS` yourself.

### Check the audit log

```bash
ADMIN="Authorization: Bearer sk-admin-change-me"

# The head of the chain: the id and hash of its newest row. Keep it somewhere
# the database's writers cannot reach.
curl -s http://localhost:8090/api/v1/audit/head -H "$ADMIN"
# {"id":1,"hash":"66fd79ce...","count":1}

# Later: verify the whole chain, and that it still contains that head.
curl -s "http://localhost:8090/api/v1/audit/verify?anchor_id=1&anchor_hash=66fd79ce..." -H "$ADMIN"
# {"valid":true,"total":1,"verified":1,...,"anchor":{"id":1,"status":"ok","rows_removed_since":0}}

# Edit a row behind the proxy's back and verify again.
docker exec llmproxy python -c "import sqlite3; c = sqlite3.connect('/app/data/endpoints.db'); c.execute('UPDATE audit_log SET status = 200 WHERE id = 1'); c.commit()"
curl -s "http://localhost:8090/api/v1/audit/verify?anchor_id=1&anchor_hash=66fd79ce..." -H "$ADMIN"
# {"valid":false,"total":1,"verified":0,"broken_at":1,"error":"entry_hash mismatch at id=1 (tamper detected)"}
```

Without a key the chain is plain SHA-256: it detects accidental damage and edits
like the one above, not someone who rewrites a row and recomputes the hashes after
it. Set `LLM_PROXY_AUDIT_KEY` (32+ characters, kept where the database's writers
cannot read it) to seal rows with HMAC-SHA-256; a writer of the database without
the key can then neither alter, remove nor add rows unnoticed. The proxy writes the
head to its log hourly (`AUDIT HEAD ...`).

## What is recorded, and what is not

| In the audit chain | Not in the chain |
|---|---|
| Every chat, completion and embedding request that reaches the pipeline: caller, model, provider, status, tokens, cost, latency | Prompts and responses (the log holds metadata, not content) |
| Requests the shield, a plugin or the tool policy refused, with the reason | Requests rejected before the pipeline: a wrong key, the rate limiter, the byte firewall |
| Requests that failed upstream | Control-plane changes (configuration, toggles, plugin installs) |
| Retention purges and GDPR erasures, as removal records | |

The caller is recorded as the first eight characters of the API key, or the
signed-in user for an identity token. Keys that share their first eight characters
are not distinguished. `LLM_PROXY_IDENTITY_SECRET` must be set for session
identifiers to stay the same across restarts.

## Security controls

| Control | Default | What it does |
|---|---|---|
| Authentication | on | API keys in two tiers (inference, admin); RBAC with four roles. OIDC/JWT sign-in is available and off by default. |
| Byte firewall | on | 178 signatures, matched after decoding URL, Unicode, Base64, hex and ROT13 encodings. Runs before authentication. |
| Shield | on | 30 scoring patterns, a 157-entry character-trigram corpus (lexical similarity, not embeddings), per-session trajectory. |
| PII masking | on | Regular expressions for email, phone, SSN, card, IBAN, IP and API keys; Presidio (11 entity types) when installed. Applied to chat messages. |
| Response sanitisation | on | Injection patterns, invisible characters and block-listed link domains, on non-streaming responses. Streams are not sanitised. |
| Audit chain | on | See above. Keyed only when `LLM_PROXY_AUDIT_KEY` is set. |
| Tool policy | off | Which tools a response may call, and which may follow a tool result. |
| Rate limiting | off | Token bucket per IP and key (`rate_limiting.enabled`). |
| Response signing | off | HMAC over non-streaming responses when `LLM_PROXY_SIGNING_KEY` is set. A shared-secret signature: it tells the holder of the key the response was not altered. |

GDPR endpoints export and erase a subject's rows and purge rows past the retention
period (90 days by default). They are tools for the operator; they do not make a
deployment compliant.

### What the detection layer stops, measured

<!-- waf-bench:start -->
Measured 2026-10-10 on llmproxy 1.38.1, default configuration, 5,147 prompts from four public datasets, one indirect-injection benchmark and a held-out set:

| | Stopped |
|---|---|
| Attack prompts (3,035) | **21%** |
| of which: an instruction hidden in a tool result (1,054) | **0%** |
| Benign prompts (2,112), stopped by mistake | **0.1%** |

With the [tool policy](docs/security/tool-policy.md) on (read-only tools after a tool result), the call the planted instruction asks for is refused in **1,054 of 1,054** of those cases, and 0 of the 1,054 calls the users' own tasks need. That figure assumes the model obeys the instruction; no text is read to reach it.
<!-- waf-bench:end -->

The firewall and the shield recognise known phrasings and their encodings, quickly
and with almost no false positives. They miss a reworded attack and an instruction
planted in a document or a tool result. An open classifier stops far more of the
same prompts and refuses a large share of legitimate ones. Neither is a reason to
trust model output: restrict what a response can do rather than rely on spotting the
attack. Per-dataset results, method and reproduction:
[docs/security/benchmark.md](docs/security/benchmark.md).

The regression corpus in `tests/corpus/` (report in
[docs/OWASP_LLM_COVERAGE.md](docs/OWASP_LLM_COVERAGE.md)) was written alongside the
detector. It shows that a build did not get worse on prompts the detector already
knew; it is not a measure of detection.

Vulnerability reports: [SECURITY.md](SECURITY.md).

## Request path

```
Client request
  rate limiter            off by default
  byte firewall           before authentication
  authentication          inference keys on /v1/*, admin keys on /api/v1/* and /admin/*
  shield                  injection scoring, trajectory
  ring 1  ingress         plugins
  ring 2  pre-flight      PII masking, budget guard, cache lookup
  ring 3  routing         endpoint selection
  upstream                format translation, fallback chain, circuit breaker
  tool policy             on the response's tool calls
  ring 4  post-flight     response sanitisation
  ring 5  background      telemetry, cache write
Client response
```

Endpoints are chosen by `success_rate^2 / latency`, weighted by price
(`cost_weight`). A request that fails with a retryable upstream error moves to the
next entry of the model's fallback chain; a request whose upstream accepted it and
then timed out is not sent again. When the day's spend reaches `budget.daily_limit`
requests are refused with `402`.

## Performance

Measured on an Apple Silicon laptop, one process, authentication off and no
upstream call, so these are the cost of the proxy itself and an order of magnitude,
not a guarantee:

| Endpoint | Requests/s | p50 | p99 | Load |
|---|---:|---:|---:|---|
| `/api/v1/registry` (middleware chain and a database read) | 1,158 | 81 ms | 188 ms | wrk, 4 threads, 100 connections, 30 s |
| `/health` | 1,313 | 7 ms | 28 ms | wrk, 2 threads, 10 connections, 20 s |

The per-request cost of the security checks is in
[docs/PERFORMANCE.md](docs/PERFORMANCE.md). A real request is dominated by the
provider's latency.

**Run one instance.** The daily budget total, per-session scoring, the kill switch
and other state live in the process; a second replica enforces its own copy of each
and nothing detects it. The Helm chart pins `replicaCount` to 1 for this reason.
Rate limiting and the circuit breaker can share state through Redis; the rest
cannot yet.

## API

OpenAI-compatible, on port 8090.

| Endpoint | Method | |
|---|---|---|
| `/v1/chat/completions` | POST | Chat completion, streaming or not |
| `/v1/completions` | POST | Legacy text completion |
| `/v1/embeddings` | POST | Embeddings (every adapter except Anthropic) |
| `/v1/models` | GET | Models of the configured endpoints |
| `/health`, `/ready` | GET | Liveness and readiness |
| `/metrics` | GET | Prometheus metrics (admin key) |

Control plane (admin key):

| Endpoint | Method | |
|---|---|---|
| `/api/v1/audit` | GET | Query the audit log |
| `/api/v1/audit/head` | GET | Head of the chain |
| `/api/v1/audit/verify` | GET | Verify the chain, optionally against a recorded head |
| `/api/v1/gdpr/export/{subject}` | GET | A subject's rows |
| `/api/v1/gdpr/erase/{subject}` | POST | Erase a subject's rows |
| `/api/v1/gdpr/purge` | POST | Purge rows past retention now |
| `/api/v1/registry` | GET, POST | Endpoints |
| `/api/v1/panic` | POST | Stop serving inference on every route |
| `/api/v1/features/toggle` | POST | Turn a guard on or off |
| `/api/v1/analytics/spend` | GET | Spend by model, provider, key, date |
| `/api/v1/plugins` | GET | Installed plugins |

The full reference is in [docs/api](docs/api/).

## Configuration

```yaml
server:
  port: 8090
  timeout: 30s              # longest silence on a streamed response
  response_timeout: 600s    # longest a non-streaming response may take
  auth: { enabled: true, api_keys_env: "LLM_PROXY_API_KEYS", admin_keys_env: "LLM_PROXY_ADMIN_KEYS" }

endpoints:
  openai:
    provider: "openai"
    base_url: "https://api.openai.com/v1"
    api_key_env: "OPENAI_API_KEY"
    models: ["gpt-4o", "gpt-4o-mini"]

fallback_chains:
  "gpt-4o":
    - { provider: anthropic, model: "claude-sonnet-4-20250514" }

budget:
  daily_limit: 50.0

security:
  tool_policy:
    enabled: true
    after_tool_result: ["*Get*", "*Read*", "*Search*", "*List*"]
```

Secrets are read from the environment. [config.yaml](config.yaml) is the shipped
configuration and [docs/reference/config.md](docs/reference/config.md) the reference.

## Plugins

Requests pass through five rings (ingress, pre-flight, routing, post-flight,
background). The defaults live in `plugins/default/`; `plugins/marketplace/` holds
18 marketplace plugins, most of them off (budget guard, loop breaker, model
downgrade, topic blocklist, schema enforcement, canary detection, shadow traffic
and others). Each plugin declares whether a failure refuses the request or lets it
through.

```python
from core.plugin_sdk import BasePlugin, PluginResponse, PluginHook

class MyPlugin(BasePlugin):
    name = "my_plugin"
    hook = PluginHook.PRE_FLIGHT
    version = "1.0.0"

    async def execute(self, ctx):
        return PluginResponse.passthrough()
```

Python plugins run in the proxy's process and are not sandboxed. WASM plugins need
the `extism` package, which the published image does not include. See
[plugins/](plugins/).

## Admin UI

At `/ui`: threats and live events, guards, plugins, models, spend, audit
verification and GDPR actions, endpoints with circuit state, live logs, settings.

## Observability

- **Prometheus**: requests, errors, latency, time to first token, tokens, cost,
  budget, circuit state, blocks, auth failures, plugin failures, stream outcomes,
  tool-policy refusals. A Grafana dashboard and alert rules are in `monitoring/`.
- **Webhooks**: Slack, Teams, Discord, generic JSON, and ECS JSON for a SIEM; signed
  with HMAC-SHA-256 when a secret is configured.
- **Tracing and errors**: OpenTelemetry (OTLP) and Sentry, when configured.

## Development

```bash
make test         # the suite CI runs
make test-pg      # the same against a throwaway Postgres
make lint         # ruff
make typecheck    # mypy
make waf-bench    # the detection benchmark (downloads about 750 MB)
```

CI runs lint, type check, dependency audit, a lockfile check, secret scan, supply
chain checks, the test suite with a coverage gate of 73%, property-based tests and
an image build; the image is published only when all of them pass.

## Before production

| | Shipped | Do |
|---|---|---|
| Admin keys | unset | Set `LLM_PROXY_ADMIN_KEYS` |
| Audit key | unset | Set `LLM_PROXY_AUDIT_KEY`; keep a copy off the host |
| Session identifiers | per-process | Set `LLM_PROXY_IDENTITY_SECRET` |
| TLS | off | Terminate TLS in front of the proxy |
| CORS | localhost only | Set `server.cors_origins` for your UI origin |
| Tool policy | off | Turn it on in `log_only` mode first |
| Rate limiting | off | Enable `rate_limiting`, or limit upstream of the proxy |
| Backups | none | `scripts/backup_db.py`; the data volume holds the audit log |

## License

MIT. See [LICENSE](LICENSE).

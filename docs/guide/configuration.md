# Configuration

LLMProxy reads `config.yaml` from its working directory, or the file named by the `CONFIG_FILE` environment variable. The file is watched while the proxy runs; see [Hot Reload](#hot-reload) for what a reload does and does not re-read.

## Server

```yaml
server:
  host: 0.0.0.0
  port: 8090
  timeout: 30s             # longest silence on a streamed response
  response_timeout: 600s   # longest a non-streaming response may take
  keep_alive: 60s
  tls:
    enabled: false
    cert_file: "/etc/llmproxy/certs/server.crt"
    key_file: "/etc/llmproxy/certs/server.key"
    min_version: "1.3"     # "1.2" or "1.3"; "1.2" when the key is absent
  auth:
    enabled: true
    api_keys_env: "LLM_PROXY_API_KEYS"
    admin_keys_env: "LLM_PROXY_ADMIN_KEYS"
```

These are the values in the shipped `config.yaml`. With `tls.enabled: false` the listener serves plain HTTP and the proxy logs a warning at startup; with `tls.enabled: true` it refuses to start if the certificate or key is not set or cannot be loaded.

When `response_timeout` expires the caller gets 504 and the request is not sent to another provider.

### The two key tiers

`api_keys_env` names the variable holding **inference** keys — what `/v1/*`
accepts. `admin_keys_env` names the **control-plane** keys, and those are the
only ones `/api/v1/*`, `/admin/*` and `/metrics` accept: config apply, plugin
install, registry writes, RBAC, GDPR purge.

Leave `LLM_PROXY_ADMIN_KEYS` unset and the proxy falls back to the inference
keys for the control plane too, so every key you hand an application team can
also rewrite the configuration and purge the audit log. The proxy logs a
warning about it at startup and starts.

`enabled` defaults to **true** when the key is absent. With authentication on,
the process exits at startup if the variable named by `api_keys_env` is unset.
For local work, `LLM_PROXY_DEV_MODE=1` disables authentication on every route
and logs a warning.

### Precedence

Values resolve in this order, later winning:

1. `config.yaml` — in a container, the image's copy unless you mount over it.
2. Environment overlays applied after the parse: `LLM_PROXY_ENDPOINT_<NAME>_*`
   declarations are added to the endpoint map (an endpoint with the same id in
   `config.yaml` wins over the environment one), `LLM_PROXY_FIREWALL_ENABLED`
   overwrites `security.firewall.enabled`, and `LLM_PROXY_DEV_MODE` overwrites
   `server.auth.enabled`. These re-apply on every reload, so an env value
   cannot be edited away in YAML.
3. Variables read directly where they are used and never merged into the config
   — `LLM_PROXY_DB_PATH`, `LLM_PROXY_REDIS_TIMEOUT`, `REDIS_URL`,
   `LLM_PROXY_AUDIT_KEY`, `LLM_PROXY_SIGNING_KEY`, the two key bags, and each
   endpoint's `api_key_env`.
4. Runtime changes through the admin API (`/api/v1/routing/cost-weight`,
   `/api/v1/features/toggle`, `/api/v1/proxy/toggle`,
   `/api/v1/rate-limit/preset`). They are stored in the database, restored at
   the next start, and take precedence over the config value.

`GET /api/v1/config/raw` returns layer 1 — the file — so it can differ from
what the process is running.

## Endpoints

Each endpoint maps to an LLM provider with its adapter:

```yaml
endpoints:
  openai:
    provider: "openai"
    base_url: "https://api.openai.com/v1"
    api_key_env: "OPENAI_API_KEY"
    models: ["gpt-4o", "gpt-4o-mini", "text-embedding-3-small"]

  anthropic:
    provider: "anthropic"
    base_url: "https://api.anthropic.com/v1"
    api_key_env: "ANTHROPIC_API_KEY"
    models: ["claude-sonnet-4-20250514", "claude-haiku-4-5-20251001"]
```

An endpoint that names an `api_key_env` is registered for routing only when
that variable is set. See [Endpoints Reference](/reference/endpoints) for the
provider names the adapter registry accepts and for how endpoints reach the
routing pool.

The shipped `config.yaml` also carries a `rate_limit: { rpm, tpm }` entry on
some endpoints. No code reads it.

## Fallback Chains

When the selected endpoint has an open circuit, cannot be reached, or answers
429, 500, 502, 503 or 504, the request is retried against the chain declared
for the requested model, in order:

```yaml
fallback_chains:
  "gpt-5.4":
    - provider: anthropic
      model: "claude-opus-4-6"
    - provider: google
      model: "gemini-2.5-pro"
```

`provider` is matched against the endpoint id or its `provider` field. Any
other upstream status is returned to the caller as it is. A non-streaming
request whose response does not arrive within `server.response_timeout` is not
retried; the caller gets 504.

A fallback attempt is sent with the fallback endpoint's own key, the one named
by its `api_key_env`.

## Model Aliases

Shorthand names that resolve to real model IDs (a subset of the shipped list):

```yaml
model_aliases:
  "gpt4": "gpt-4o"
  "claude": "claude-sonnet-4-6"
  "fast": "gpt-5.4-mini"
  "best": "gpt-5.4"
  "cheap": "gemini-2.5-flash-lite"
```

## Model Groups

A group name resolves to one of its models, chosen by the group's strategy:

```yaml
model_groups:
  "auto":
    strategy: "cheapest"  # cheapest, fastest, weighted, random
    models:
      - { model: "gpt-5.4-nano", provider: "openai", weight: 0.4 }
      - { model: "gemini-2.5-flash", provider: "google", weight: 0.3 }
      - { model: "claude-haiku-4-5-20251001", provider: "anthropic", weight: 0.2 }
      - { model: "llama-3.3-70b-versatile", provider: "groq", weight: 0.1 }
```

`cheapest` compares input-token prices, `fastest` compares measured latency,
`weighted` draws at random using `weight`, and `random` (the default when
`strategy` is absent) draws uniformly. Models whose provider has no usable key
are left out, unless that would leave none.

## Endpoint selection

For a request that does not go through a model group, the router takes the
stored endpoints with status verified, drops those whose circuit is open, and
keeps the ones that list the requested model. If none lists it, endpoints with
an empty `models` list are used; if there are none of those either, the
request is answered with 503.

Among the candidates, the highest `priority` wins when priority mode is on
(`POST /api/v1/proxy/priority/toggle`). Otherwise the router scores endpoints
on measured latency, success rate and model price, weighted by
`routing.cost_weight` (default `0.3`, adjustable at runtime through
`POST /api/v1/routing/cost-weight`), and uses round-robin until it has
measurements.

The `rotation:` section in the shipped `config.yaml` (`strategy`, `failover`)
is not read by any code.

## Budget

```yaml
budget:
  daily_limit: 50.0    # Hard cap per day (USD)
  soft_limit: 40.0     # Webhook warning threshold (USD)
```

A request is refused with 402 when today's spend plus the request's estimated
cost reaches `daily_limit`. There is no automatic downgrade to another model.
At or above `soft_limit`, completed chat requests dispatch a
`budget_threshold` webhook event. `soft_limit` must not exceed `daily_limit`.

The running total is kept per process, written to the store, and restored at
startup if the stored date is still today.

The shipped `config.yaml` also sets `budget.fallback_to_local_on_limit`. No
code reads it.

## Audit and retention

```yaml
gdpr:
  auto_purge: true        # default true: delete old audit/spend rows daily
  retention_days: 90      # default 90

audit:
  head_log_interval_seconds: 3600   # default 3600; 0 turns it off
```

`gdpr.auto_purge` runs a daily job that deletes audit and spend rows older than
`gdpr.retention_days`. The audit hash chain stays verifiable across it: each purge
and each erasure appends a row to the chain that records the hashes around the
removed rows (see the [audit API](/api/admin#audit-integrity)). Both values are
read at startup.

Without `LLM_PROXY_AUDIT_KEY` the audit chain is plain SHA-256, so it detects
accidental damage and unrecorded edits, not someone who can write the database and
recomputes it; with the key they still can cut off the newest rows. To cover that,
the proxy writes an `AUDIT HEAD id=... hash=... count=...` line to the process log
(standard error, which is the container log under Docker) every
`audit.head_log_interval_seconds`. It is not sent to the SIEM or
webhook exports. Keep one of those lines (or `GET /api/v1/audit/head`) somewhere the
database's writers cannot reach, and later check the chain against it with
`GET /api/v1/audit/verify?anchor_id=<id>&anchor_hash=<hash>`.

## Quotas

Per-key quotas and their consumed budget are kept in a small SQLite file:

```yaml
rbac:
  db_path: data/rbac.db    # default; keep it inside the data volume
```

If the file holds no quotas and an `endpoints.db` with a `quotas` table exists
in the working directory, those rows are copied in once at startup. Back
`data/rbac.db` up with the rest of `data/` (see the deployment guide).

## Security

```yaml
security:
  enabled: true
  max_payload_size_kb: 512
  max_messages: 50
  link_sanitization:
    enabled: true
    blocked_domains: ["malicious-site.com"]
```

`max_payload_size_kb` is the request-body limit; a larger body is refused with
413. The other `security` keys are listed in the
[reference](/reference/config#security).

## Rate Limiting

Off by default: the shipped `config.yaml` has no `rate_limiting` section and
`enabled` defaults to `false`.

```yaml
rate_limiting:
  enabled: true
  requests_per_minute: 60   # default 60
  burst: 10                 # default 10
```

The limit applies per bearer token, or per client IP when the request carries
no token. `/health`, `/ready` and `/metrics` are exempt. A refused request gets
429 with a `Retry-After` header. The section is read at startup.

## Hot Reload

The proxy checks `config.yaml` every 30 seconds. A changed file is validated
with the startup checks; if it fails, the running configuration is kept and the
error is logged. To reload at once:

```bash
curl -X POST http://localhost:8090/api/v1/admin/reload \
  -H "Authorization: Bearer your-admin-key"
```

This route loads the file without running the startup checks.

A reload replaces the configuration the request path reads on every request:
fallback chains, aliases, groups, budget, tool policy, link sanitization and
the other SecurityShield settings, the webhook targets, and the endpoint map
that `/v1/models`, fallback and `/v1/embeddings` read. The file watcher also
applies circuit-breaker thresholds, the cache TTL and the plugin set.

The routing pool is not rebuilt by a reload. Endpoints are copied from the
configuration into the store at startup, and an endpoint whose id is already
stored is not updated from the file.

The following are built once at startup and need a restart to change: the
listener (`server.host`, `port`, `tls`, `keep_alive`), the byte firewall switch
and its limits (`security.firewall.enabled`, `max_payload_size_kb`,
`max_nesting_depth`), `rate_limiting`, `admission`, `server.cors_origins`,
`server.storage`, `server.metrics`, the retention and audit-head intervals, and
the upstream timeouts and `connection_pool` once the first upstream request has
been made.

### Dangerous deltas need a confirm token

`POST /api/v1/config/apply` validates a proposed configuration, saves a
timestamped copy of the current file beside it, writes the new one and
reloads. Most changes apply with a plain admin bearer — but
posture-lowering transitions (`server.auth.enabled` → `false`,
`security.firewall.enabled` → `false`, clearing
`security.link_sanitization.blocked_domains`, or raising
`security.max_payload_size_kb` to more than 4x its current value) are rejected
with 403 and `"confirm_required": true` unless the apply carries a single-use
confirm token bound to the exact proposed text. Rejections, minted tokens and
applies are written to the in-app event log (level SECURITY) with a short
identifier of the caller. That log is held in process memory and shown in the
admin UI; these entries are not rows of the audit chain.

```bash
# 1. Mint (fails 400 when the proposal has no dangerous deltas)
curl -X POST http://localhost:8090/api/v1/config/confirm-token \
  -H "Authorization: Bearer your-admin-key" \
  -H "Content-Type: application/json" \
  -d '{"yaml": "...proposed config..."}'
# → {"confirm_token": "exp.sha.nonce.sig", "expires_in": 120, "deltas": [...]}

# 2. Apply with the token (single-use, 120 s TTL, hash-bound)
curl -X POST http://localhost:8090/api/v1/config/apply \
  -H "Authorization: Bearer your-admin-key" \
  -H "Content-Type: application/json" \
  -d '{"yaml": "...same text...", "confirm_token": "exp.sha.nonce.sig"}'
```

The token is signed with a random per-process secret, never with an API key, so
a restart invalidates outstanding tokens. Set `security.confirm.signing_secret`
to use a fixed secret instead; the live-log token (`/api/v1/logs/token`) works
the same way with `security.sse.signing_secret`.

`apply` has to write the config file. The shipped `docker-compose.yml` and the
Helm chart mount `config.yaml` read-only, so in those deployments `apply`
answers 500 and the file has to be changed at its source.

Scope note: this is confirmation-of-intent, not a second privilege tier — a
stolen admin bearer can still mint (two requests instead of one). Separation
of privilege comes from segregated admin keys and rotation. `POST
/api/v1/config/validate` reports `dangerous_deltas` so editors can warn before
the apply.

For the key-by-key reference, see [Reference: Configuration](/reference/config).

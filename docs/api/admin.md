# API: Admin & Registry

Control-plane endpoints: proxy state, the endpoint registry, configuration,
analytics, the audit log and data-protection requests.

## Authentication and permissions

While authentication is on (`server.auth.enabled`, the default), everything under
`/api/v1/` requires an **admin credential**, except the three public identity
routes (`/api/v1/identity/config`, `/exchange` and `/me`). An admin credential is
one of:

- a key from `LLM_PROXY_ADMIN_KEYS`. It holds every permission. **If
  `LLM_PROXY_ADMIN_KEYS` is not set, the inference keys in `LLM_PROXY_API_KEYS`
  are accepted here instead**;
- when `identity.enabled` is true (off by default), a provider JWT or a proxy
  session token whose role holds the permission named for the route. See the
  [Identity API](/api/identity) for roles;
- when `server.admin_auth.oidc_enabled` is true (off by default), a JWT verified
  with `server.admin_auth.jwt_secret`, carrying `server.admin_auth.required_role`
  if one is set. It holds every permission.

With authentication off, none of this is checked and every route is open.

The permission is decided by path prefix and method (`core/control_plane_policy.py`):
`GET` and `HEAD` need the read permission of the prefix, every other method the
write permission. A path with no rule needs `users:manage`, which only the `admin`
role holds. Each route below names the permission it needs.

Failures are `401` (no or bad credential) and `403` (authenticated, role lacks the
permission). Responses use FastAPI's default shape, `{"detail": ...}`; only the
OpenAI-compatible `/v1/` routes use the OpenAI error envelope (see
[Errors](/api/proxy#errors)).

Routes that destroy or reset state are marked **destructive**; none of them asks
for confirmation, so they are safe to script only against the right instance.

## Proxy Control

### Toggle Proxy

```
POST /api/v1/proxy/toggle
```

Permission `proxy:toggle`. Turns request serving on or off.

```json
{"enabled": false}
```

`enabled` is required and must be a boolean (`400` otherwise). Returns
`{"status": "STOPPED", "enabled": false}`, or `"ACTIVE"` when enabled. While
stopped, `/v1/chat/completions`, `/v1/completions` and `/v1/embeddings` answer
`503`. Requests already in progress are not interrupted. The state is stored and
survives a restart.

### Proxy Status

```
GET /api/v1/proxy/status
```

Permission `registry:read`. Returns `{"enabled": true, "priority_mode": false}`.

### Priority Steering

```
POST /api/v1/proxy/priority/toggle
```

Permission `proxy:toggle`. Body `{"enabled": true}`; a missing `enabled` counts as
`false`. Returns `{"enabled": true}`. While on, the router sends each request to
the healthy endpoint with the highest registry priority. Stored; survives a
restart.

### Emergency Kill Switch

```
POST /api/v1/panic
```

Permission `users:manage`. No body. Stops request serving exactly as
`proxy/toggle` with `enabled: false` does, and dispatches the `panic_activated`
webhook event (delivered only if webhooks are enabled and an endpoint subscribes
to it). Returns `{"status": "HALTED"}`. To resume, call `proxy/toggle` with
`enabled: true`.

### Reload Config

```
POST /api/v1/admin/reload
```

Permission `users:manage`. No body. Reads `config.yaml` again, replaces the
configuration held in memory, and rebuilds the webhook dispatcher and the shield.
Returns `{"status": "reloaded", "changed": true}` (`changed` is false when the
file is unchanged); `500` if the file cannot be loaded, in which case the previous
configuration stays in place.

Not everything is re-read. Components built at start keep their start-time
settings until a restart: the firewall switch, the payload size limit, the rate
limiter and the identity providers. The rebuilt shield starts with an empty
per-session memory and threat ledger.

## Registry (Endpoints)

Reads need `registry:read`; every other call needs `registry:write`, including
`DELETE`.

### List All Endpoints

```
GET /api/v1/registry
```

Returns a JSON array, one object per stored endpoint:

```json
[
  {
    "id": "openai",
    "name": "api.openai.com",
    "url": "https://api.openai.com/v1",
    "status": "Live",
    "latency": "120ms",
    "priority": 0,
    "models": ["gpt-4o"],
    "type": "Generic",
    "circuit_state": "closed",
    "failure_count": 0,
    "failure_threshold": 5
  }
]
```

`status` is `Live` for an endpoint in use, otherwise `FOUND`, `DISCOVERED` or
`IGNORED`. `latency` is `--` until one has been measured.

### Add Endpoint

```
POST /api/v1/registry
```

```json
{
  "id": "my-ollama",
  "url": "http://10.0.0.5:11434/v1",
  "provider": "openai-compatible",
  "models": ["llama3.3"],
  "priority": 0,
  "api_key": ""
}
```

`id` (1-64 characters of `a-z`, `0-9`, `.`, `_`, `-`) and `url` (`http` or `https`,
no credentials, query or fragment) are required. `provider` defaults to
`openai-compatible`, `priority` to `0` (clamped to -1000..1000). `models` is a list
or a comma-separated string. The endpoint is stored and usable at once. Returns
`{"status": "added", "id": "...", "models": [...]}`; `400` for an invalid field,
`409` if the id or the URL is already registered.

An `api_key` is kept in the process environment only. It is not written to disk
and is lost on restart; set the provider key in the environment for a durable
endpoint.

### Probe Endpoint

```
POST /api/v1/registry/{id}/probe
```

Sends `GET <endpoint url>/models` with a 3-second timeout and returns
`{"id", "ok", "status", "latency_ms", "models_count", "url"}`, plus `detail` on
failure. It does not run inference. A `200` marks the endpoint `Live` and records
the latency; any other outcome marks it `DISCOVERED`, which takes it out of
routing. `404` for an unknown id.

### Toggle Endpoint

```
POST /api/v1/registry/{id}/toggle
```

No body. Flips the endpoint between in use and `IGNORED` and returns
`{"id": "...", "status": "..."}`. `404` for an unknown id.

### Set Priority

```
POST /api/v1/registry/{id}/priority
```

Body `{"priority": 10}`: an integer, clamped to -1000..1000 (`400` if not a
number). Returns `{"id": "...", "priority": 10}`. Priority is used when priority
steering is on. `404` for an unknown id.

### Delete Endpoint

```
DELETE /api/v1/registry/{id}
```

**Destructive.** Removes the endpoint from the store and from the live
configuration. Returns `{"status": "deleted"}`, also when the id did not exist.

## Features

### List Feature Flags

```
GET /api/v1/features
```

Permission `registry:read`. Returns the three flags, for example
`{"language_guard": true, "injection_guard": true, "link_sanitizer": true}`. All
three are on by default.

### Toggle Feature

```
POST /api/v1/features/toggle
```

Permission `features:toggle`.

```json
{
  "name": "injection_guard",
  "enabled": true
}
```

Returns `{"name": "injection_guard", "enabled": true}`. If `enabled` is omitted
the flag is inverted. `400` for an unknown name. The value is stored and applied
again at start.

What each flag controls:

| Flag | Effect |
|------|--------|
| `language_guard` | The character-set check on non-streaming responses. |
| `injection_guard` | The injection-pattern check on non-streaming **responses**. It does not switch off the scoring of requests. |
| `link_sanitizer` | None. The flag is stored and reported, but no check reads it. Link checks are configured with `security.link_sanitization` in `config.yaml`. |

A configuration reload (`POST /api/v1/admin/reload`, `POST /api/v1/config/apply`,
or a change to `config.yaml` picked up by the file watcher) rebuilds the shield
with both response checks on. `GET /api/v1/features` keeps reporting the stored
value; set the flag again, or restart, for it to apply.

## Analytics

Both need permission `users:manage`.

### Spend Breakdown

```
GET /api/v1/analytics/spend
```

**Params:** `from`, `to` (dates), `group_by` (`model`, `provider`, `key_prefix` or
`date`; default and fallback `model`), `limit` (default 50, 1 to 1000).

Returns `{"total": ..., "breakdown": [...], "routing": {...}, "forecast": {...}}`.

### Top Models by Spend

```
GET /api/v1/analytics/spend/topmodels
```

**Params:** `limit` (default 10, 1 to 200). Returns the spend grouped by model, as
an array.

## Audit Log

```
GET /api/v1/audit
```

Permission `logs:read`.

**Params:** `from`, `to` (ISO 8601 date or datetime; `400` if malformed), `model`,
`key_prefix` (exact match), `status` (HTTP status), `blocked` (`0` or `1`), `limit`
(default 100, 1 to 1000), `offset`.

Returns `{"total": N, "items": [...]}`. Rows hold request metadata (time, caller,
model, provider, status, tokens, cost, latency, whether and why the request was
refused), not prompts or responses. What is and is not recorded is set out in the
[threat model](/threat_model#_3-3-repudiation).

## Injection Corpus

```
GET /api/v1/security/corpus
```

Permission `logs:read`. Statistics of the phrase list the shield compares prompts
against by character trigrams:
`{"total_patterns": 157, "categories": {"override": 17, ...}, "ngram_size": 3, "method": "trigram_jaccard_sliding_window"}`.
The count is that of the loaded `data/injection_corpus.yaml` (157 entries as
shipped).

## Data Export Files

```
GET /api/v1/export/files/{filename}
```

Permission `logs:read`. Downloads a file from the export directory
(`observability.export.output_dir`). Export is off by default; the route answers
`404` when it is off or the file does not exist, and `400` for a path outside the
export directory.

## System Info

| Endpoint | Permission | Description |
|----------|-----------|-------------|
| `GET /api/v1/version` | `logs:read` | `{"version": "..."}` |
| `GET /api/v1/service-info` | `users:manage` | `host` (the address the request came from), `port` (`server.port`) and a `url` built from the two |
| `GET /api/v1/network/info` | `users:manage` | `host`, `port`, `version`, and `tailscale_active`, which is true when `server.host` is neither `0.0.0.0` nor `127.0.0.1`; Tailscale itself is not queried |
| `GET /api/v1/cache/stats` | `logs:read` | Negative-cache and response-cache counters |
| `GET /api/v1/guards/status` | `users:manage` | Feature flags, circuit breaker states, firewall counters, rate-limiter setting, budget, threat-ledger counters, whether response signing is on |
| `GET /api/v1/metrics/latency` | `logs:read` | Per-ring and per-plugin latency percentiles and time to first token, over recent requests |
| `GET /api/v1/metrics/ring-timeline` | `logs:read` | The 20 most recent request traces |
| `GET /api/v1/webhooks` | `users:manage` | Whether webhooks are enabled, the configured endpoints (URL truncated to 20 characters) and the event types |
| `GET /api/v1/export/status` | `logs:read` | `{"enabled": false}`, or the export directory, its settings and its ten most recent files |
| `GET /api/v1/rbac/roles` | `users:manage` | The role-to-permission matrix |

## Configuration

All six need permission `proxy:config`, which only the `admin` role holds.

| Route | Description |
|-------|-------------|
| `GET /api/v1/config/yaml` | The active config rendered as YAML, secrets redacted. |
| `GET /api/v1/config/raw` | The on-disk `config.yaml` source, for the editor. |
| `GET /api/v1/config/warnings` | Startup-validation warnings (the same ones logged at boot). |
| `POST /api/v1/config/validate` | Dry-run: validate a proposed config, write nothing. |
| `POST /api/v1/config/confirm-token` | Mint a single-use token for a posture-lowering apply. |
| `POST /api/v1/config/apply` | Validate, back up, write atomically and reload. |

`validate`, `confirm-token` and `apply` take `{"yaml": "<full config text>"}`
(at most 256 KiB, `413` above it). `validate` returns
`{"valid": bool, "errors": [...], "warnings": [...], "dangerous_deltas": [...]}`
and changes nothing.

`apply` validates first (`400` with `errors` and `warnings` if the text is not
valid), writes a timestamped backup (`config.yaml.bak.<epoch>`) and then replaces
the file atomically. It returns
`{"applied": true, "warnings": [...], "backup": "config.yaml.bak.<epoch>"}`. If
the new config fails to load it restores the previous file and answers `500`. It
reads and writes the file the proxy was started with, so that path must be a
writable **directory mount**, not a single bind-mounted file (the rename fails on
a single-file mount).

The reload after an apply is the one `POST /api/v1/admin/reload` performs, with
the same limits: a setting that is read only at start, such as the firewall
switch or the payload size limit, takes effect at the next restart.

Changes that lower the security posture (`auth-disabled`, `firewall-disabled`,
`blocklist-cleared`, `payload-widened`: the cap raised more than 4x) are refused
with `403` and a body carrying `"confirm_required": true` and the list of
`deltas`, unless the request carries `confirm_token`. `confirm-token` mints one
for the SHA-256 of that exact text and returns
`{"confirm_token": "...", "expires_in": 120, "deltas": [...]}`; it answers `400`
when the text is invalid or contains no such change. A token is valid for 120
seconds and for one use. Minting, a refused apply and a successful apply are
written to the operator log (level `SECURITY`, `GET /api/v1/logs`). The operator
log is held in memory; these events are not rows in the audit chain, and with
authentication on the entry does not identify which key or user made the change.

## Reset and clear (destructive)

| Route | Permission | Effect |
|-------|-----------|--------|
| `POST /api/v1/cache/clear` | `features:toggle` | Empties the negative cache and evicts expired positive-cache entries. Returns `{"status": "cleared", "negative_cache": "cleared N entries", ...}`; `502` if eviction fails. |
| `POST /api/v1/security/reset` | `features:toggle` | **Destructive.** Clears the shield's per-session memory and the threat ledger (per-IP and per-key aggregates). Multi-turn injection scoring starts from zero. Returns `sessions_cleared` and, when a ledger is configured, `threat_ledger_dropped` (`{"ips": N, "keys": N}`). |
| `POST /api/v1/firewall/reset` | `users:manage` | **Destructive.** Zeroes the firewall WAF counters (scanned, blocked, per-signature and per-encoding block counts, scan time). Counters only; no rule or setting changes. |
| `POST /api/v1/circuit-breaker/{endpoint_id}/reset` | `users:manage` | Forces that endpoint's breaker to CLOSED, so traffic resumes immediately. With Redis configured it clears the shared breaker state for every process; if Redis cannot be reached the answer is `502`, not a claim that the breaker is closed. |
| `POST /api/v1/webhooks/test` | `users:manage` | Sends a test payload to every configured webhook. |

## Data protection (GDPR)

All need permission `users:manage`.

| Route | Description |
|-------|-------------|
| `GET /api/v1/gdpr/retention` | The retention settings: `gdpr.retention_days` (default 90) and `gdpr.auto_purge` (default on). The response also carries `purposes`, `legal_basis` and `data_categories`: fixed informational text supplied by the software, the same for every deployment. It is not a legal determination. |
| `POST /api/v1/gdpr/purge` | **Destructive.** Deletes audit and spend records older than the retention period, now instead of waiting for the automatic purge, which runs once every 24 hours of uptime (the first time 24 hours after start). Returns `{"status": "purged", "retention_days": N, "audit_deleted": N, "spend_deleted": N}`. |
| `GET /api/v1/gdpr/export/{subject}` | Export of what is held for the subject: audit rows, spend rows and role assignments, with keys and tokens scrubbed. `404` when nothing is held. The export is itself recorded in the audit chain. |
| `POST /api/v1/gdpr/erase/{subject}` | **Destructive.** Deletes the subject's audit rows, spend rows and role assignments. |

`subject` must be at least 8 characters (`400` otherwise) and is matched exactly:
against `session_id` or `key_prefix` in the audit log, `key_prefix` in the spend
log, and `subject` or `email` in the role assignments.

`erase` writes an intent record to the audit chain *before* deleting and refuses
(`503`) if it cannot, so every erasure request leaves a trail even if the delete
then fails; it answers `404` when the subject has no data (the intent record
remains). The record carries the SHA-256 of the subject, not the subject: to
show that someone was erased, hash the identifier you hold and look for it.
Both `purge` and `erase` remove audit rows from the hash chain; each appends a
removal record to the chain itself, so `GET /api/v1/audit/verify` still reports
a valid chain and lists what was removed (`removals`, `rows_removed`). `erase`
and `export` answer `400` for the names the chain files its own rows under
(`AUDIT_SYSTEM`, `AUDIT`, `GDPR_SYSTEM`, `GDPR`).

## Audit integrity

```
GET /api/v1/audit/verify
```

Permission `logs:read`. Walks the audit hash chain and recomputes each entry's
hash. Returns `{"valid": true, "total": N, "verified": N, "broken_at": null,
"rows_removed": N, "removals": [...], "formats": {"v2": N}, "keyed": false}`, or
`valid: false` with `broken_at` (row id) and `error`. `rows_removed` counts rows
removed by a recorded retention purge or erasure and bridged over; `removals`
lists each recorded removal (`id` of the row recording it, `at`, `reason`,
`rows`; the newest hundred). `formats` counts rows per chain format and `keyed`
says whether the newest rows are sealed with `LLM_PROXY_AUDIT_KEY`. The counts
include the chain's own rows (one per purge or erasure). It checks the whole chain, a page of 5,000 rows at a time, so the time it takes grows with the length of the audit log (about a second per 100,000 rows on a laptop); `total` is the rows examined, which on a failure is the rows up to and including the one that broke.

```
GET /api/v1/audit/verify?anchor_id=<id>&anchor_hash=<64 hex>
```

Without `LLM_PROXY_AUDIT_KEY` the chain is plain SHA-256: someone who can write the database can edit a row and recompute every later hash. With or without the key they can delete the newest rows, and the chain still verifies against itself. With an **anchor**, a head you recorded earlier and kept outside the database, the chain must also still contain that row with that hash. The result gains `anchor: {"id", "status", "rows_removed_since"}` (`rows_removed_since`: rows removed by operations recorded after the anchored row) where `status` is `ok`; `purged` (older than the oldest retained row, removed by the retention purge) or `erased` (removed by a recorded erasure), both fine; or a failure: `mismatch` (the rows up to it were rewritten), `truncated` (the chain now ends before it), `missing` (gone with no recorded deletion). `400` if only one of the two parameters is given or they are malformed.

```
GET /api/v1/audit/head
```

Permission `logs:read`. Returns `{"id": N, "hash": "<64 hex>", "count": N}`: the newest audit row and the row count (`{"id": 0, "hash": "GENESIS", "count": 0}` when empty). This is the value to record elsewhere. The proxy also writes it to the process log (standard error) hourly (`AUDIT HEAD id=... hash=... count=...`, `audit.head_log_interval_seconds`).

## Runtime tuning

| Route | Permission | Description |
|-------|-----------|-------------|
| `GET /api/v1/routing/config` | `registry:read` | Live routing configuration: `cost_weight` and the active strategy. |
| `POST /api/v1/routing/cost-weight` | `features:toggle` | `{"cost_weight": 0.0-1.0}`: 0 ignores cost, 1 fully prefers cheaper models. Persisted; survives restarts. |
| `GET /api/v1/rate-limit/config` | `registry:read` | Enabled flag, active preset (if any), and the requests-per-minute and burst being served. |
| `POST /api/v1/rate-limit/preset` | `features:toggle` | `{"preset": "strict"\|"normal"\|"relaxed"}` = 30/60/240 requests per minute with burst 5/10/60. Existing buckets are flushed so the new limits apply at once. Persisted. A preset changes the limits only: it does not turn the limiter on (`rate_limiting.enabled`, off by default). `400` for an unknown preset. |
| `POST /api/v1/registry/scan` | `registry:write` | On-demand local discovery: probes the Ollama, LM Studio, vLLM and LiteLLM default ports on `127.0.0.1`, `host.docker.internal` and the hosts in `LLM_PROXY_DISCOVERY_PEERS`. Returns `{"candidates": [...], "total": N}`, leaving out endpoints that are already configured. It adds nothing to the registry. |

## Dashboards and analytics

| Route | Permission | Description |
|-------|-----------|-------------|
| `GET /api/v1/dashboard/summary` | `logs:read` | The attention list and suggested next steps behind the overview page: `now`, `attention`, `do_next`, `recent_changes`. If a section could not be built (for example the audit query failed) the rest is still returned and `section_errors` lists the missing sections: an empty `attention` is only "all clear" when `section_errors` is absent. |
| `GET /api/v1/slos` | `users:manage` | Per-endpoint error rates from the circuit breakers and the daily budget burn. |
| `GET /api/v1/analytics/forecast` | `users:manage` | Today's burn rate, projected total, headroom and time to the limit. |
| `GET /api/v1/analytics/cost-efficiency` | `users:manage` | Average cost per request and savings against a premium-model baseline. |
| `GET /api/v1/metrics/hourly-buckets` | `logs:read` | 24 hourly buckets of KPI counters and gauges. |
| `GET /api/v1/plugins/stats` | `registry:read` | Per-plugin invocation, error, timeout and latency counters. |
| `POST /api/v1/logs/token` | `logs:clear` | Mints a short-lived token (default 120 s, at most 600 s) for the browser's log `EventSource`, which cannot send headers. Returns `{"sse_token": "...", "expires_in": N}`. |
| `POST /api/v1/logs/client` | `logs:clear` | Ingest of browser log records (`{"records": [...]}`, at most 100 per batch, `413` above) into the operator log. Returns `202` with `{"accepted": N, "dropped": N}`; `404` when `security.client_logs.enabled` is false. |
| `GET /api/v1/openapi.json` | `users:manage` | The OpenAPI schema, behind admin auth (FastAPI's own `/openapi.json` is disabled when authentication is on). |

## Event streams

```
GET /api/v1/telemetry/stream
```

Permission `users:manage`. A server-sent event stream of telemetry events
(`{"type", "timestamp", "data"}`), with a keep-alive comment every second when
idle. At most 20 connections at a time (`503` beyond that). The admin UI does not
use this route.

```
GET /api/v1/logs
```

Permission `logs:read`. A server-sent event stream of the operator log. On
connect it replays the most recent entries (up to 200), then sends new ones as
they occur. A browser `EventSource` passes the token from
`POST /api/v1/logs/token` as `?sse_token=`. At most 20 connections at a time
(`503` beyond that).

The operator log is held in memory. It is where control-plane actions (a
configuration apply, a feature toggle, a plugin install) are recorded; they are
not rows in the audit chain.

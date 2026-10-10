# API: Admin & Registry

Control and configuration endpoints for proxy management.

## Authentication and permissions

Everything under `/api/v1/` requires an **admin credential**, except the three
public SSO routes (`/api/v1/identity/config`, `/exchange` and `/me`): a key from
`LLM_PROXY_ADMIN_KEYS` (which holds every permission), or an SSO session whose
role holds the permission named in the **Permission** column below. See the
[Identity API](/api/identity) for roles. Failures are `401` (no or bad
credential) and `403` (authenticated, role lacks the permission). Responses use
FastAPI's default shape, `{"detail": ...}`; only the OpenAI-compatible `/v1/`
routes use the OpenAI error envelope (see [Errors](/api/proxy#errors)).

Routes that destroy or reset state are marked **destructive**; none of them asks
for confirmation, so they are safe to script only against the right instance.

## Proxy Control

### Toggle Proxy

```
POST /api/v1/proxy/toggle
```

Enable or disable the proxy globally.

### Proxy Status

```
GET /api/v1/proxy/status
```

Returns proxy enabled state and priority mode.

### Priority Steering

```
POST /api/v1/proxy/priority/toggle
```

Toggle priority steering mode for endpoint selection.

### Emergency Kill Switch

```
POST /api/v1/panic
```

Emergency halt — stops all traffic immediately. Sends webhook notification to configured channels.

### Hot Reload Config

```
POST /api/v1/admin/reload
```

Reload `config.yaml` without restart. Zero-downtime configuration updates.

## Registry (Endpoints)

### List All Endpoints

```
GET /api/v1/registry
```

Returns full model pool state (Live / Discovered / Offline) for all configured endpoints.

### Probe Endpoint

```
POST /api/v1/registry/{id}/probe
```

Runs a low-cost `GET /v1/models` health probe against the endpoint, refreshes status/latency/model metadata, and returns the probe result. It does not run inference.

### Toggle Endpoint

```
POST /api/v1/registry/{id}/toggle
```

Enable or disable a specific endpoint.

### Set Priority

```
POST /api/v1/registry/{id}/priority
```

Set endpoint routing priority.

### Delete Endpoint

```
DELETE /api/v1/registry/{id}
```

Remove an endpoint from the registry.

## Features

### List Feature Flags

```
GET /api/v1/features
```

Returns security feature flags: `language_guard`, `injection_guard`, `link_sanitizer`.

### Toggle Feature

```
POST /api/v1/features/toggle
```

```json
{
  "feature": "injection_guard",
  "enabled": true
}
```

## Analytics

### Spend Breakdown

```
GET /api/v1/analytics/spend
```

**Params:** `from`, `to`, `group_by` (model/provider/key/date), `limit`

### Top Models by Spend

```
GET /api/v1/analytics/spend/topmodels
```

## Audit Log

```
GET /api/v1/audit
```

**Params:** `from`, `to`, `model`, `key_prefix`, `status`, `blocked`

Persistent audit log with PII masking.

## Security Corpus

```
GET /api/v1/security/corpus
```

Returns active semantic injection corpus statistics loaded from the runtime analyzer, including category counts and pattern totals.

## Data Export Files

```
GET /api/v1/export/files/{filename}
```

Downloads a generated export file from the configured export directory. The path is confined to that directory.

## System Info

| Endpoint | Description |
|----------|-------------|
| `GET /api/v1/version` | Current version |
| `GET /api/v1/service-info` | Host, port, URL |
| `GET /api/v1/network/info` | Network and Tailscale status |
| `GET /api/v1/cache/stats` | Cache subsystem status |
| `GET /api/v1/guards/status` | Security subsystem status |
| `GET /api/v1/metrics/latency` | Per-ring/plugin latency P50/P95/P99 |
| `GET /api/v1/metrics/ring-timeline` | Recent request traces |
| `GET /api/v1/webhooks` | Configured webhooks |
| `GET /api/v1/export/status` | Export subsystem status |
| `GET /api/v1/rbac/roles` | RBAC role permission matrix |

## Configuration

All five need permission `proxy:config`.

| Route | Description |
|-------|-------------|
| `GET /api/v1/config/yaml` | The active config rendered as YAML, secrets redacted. |
| `GET /api/v1/config/raw` | The on-disk `config.yaml` source, for the editor. |
| `GET /api/v1/config/warnings` | Startup-validation warnings (the same ones logged at boot). |
| `POST /api/v1/config/validate` | Dry-run: validate a proposed config, write nothing. |
| `POST /api/v1/config/confirm-token` | Mint a single-use token for a posture-lowering apply. |
| `POST /api/v1/config/apply` | Validate, back up, write atomically and hot-reload. |

`validate`, `confirm-token` and `apply` take `{"yaml": "<full config text>"}`
(max size enforced, `413` above it). `validate` returns
`{"valid": bool, "errors": [...], "warnings": [...]}` and changes nothing.

`apply` validates first, writes a timestamped backup (`config.yaml.bak.<epoch>`)
and then replaces the file atomically; if the new config fails to load it
restores the previous file and answers `500`. It reads and writes the file the
proxy was started with, so that path must be a writable **directory mount**, not
a single bind-mounted file (the rename fails on a single-file mount).

Changes that lower the security posture (authentication off, firewall off,
blocklist cleared, payload cap widened more than 4x) are refused with
`403 confirm_required` unless the request carries a token from
`confirm-token`, minted for the SHA-256 of that exact text, valid about 120 s
and single-use. Minting and confirmed applies are audit-logged with the acting
principal.

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
| `GET /api/v1/gdpr/retention` | The retention policy: `retention_days` (default 90), `auto_purge` (default on), and the stated purposes, legal basis and data categories. |
| `POST /api/v1/gdpr/purge` | **Destructive.** Deletes audit and spend records older than the retention period, now instead of waiting for the daily job. Returns `{"status": "purged", "retention_days": N, "audit_deleted": N, "spend_deleted": N}`. |
| `GET /api/v1/gdpr/export/{subject}` | Data subject access request: everything held for the subject, with keys and tokens scrubbed. |
| `POST /api/v1/gdpr/erase/{subject}` | **Destructive.** Right to erasure: deletes the subject's audit rows, spend rows and role assignments. `subject` must be at least 8 characters. |

`erase` writes an intent record to the audit chain *before* deleting and refuses
(`503`) if it cannot, so every erasure request leaves a trail even if the delete
then fails; it answers `404` when the subject has no data (the intent record
remains). The record carries the SHA-256 of the subject, not the subject: to
show that someone was erased, hash the identifier you hold and look for it.
Both `purge` and `erase` remove audit rows from the hash chain; each appends a
removal record to the chain itself, so `GET /api/v1/audit/verify` still reports
a valid chain and lists what was removed (`removals`, `rows_removed`). `erase`
and `export` answer `400` for the names the chain files its own rows under
(`AUDIT_SYSTEM`, `GDPR_SYSTEM`).

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

Permission `logs:read`. Returns `{"id": N, "hash": "<64 hex>", "count": N}`: the newest audit row and the row count (`{"id": 0, "hash": "GENESIS", "count": 0}` when empty). This is the value to record elsewhere. The proxy also writes it to the security log hourly (`AUDIT HEAD id=... hash=... count=...`, `audit.head_log_interval_seconds`).

## Runtime tuning

| Route | Permission | Description |
|-------|-----------|-------------|
| `GET /api/v1/routing/config` | `registry:read` | Live routing configuration: `cost_weight` and the active strategy. |
| `POST /api/v1/routing/cost-weight` | `features:toggle` | `{"cost_weight": 0.0-1.0}`: 0 ignores cost, 1 fully prefers cheaper models. Persisted; survives restarts. |
| `GET /api/v1/rate-limit/config` | `registry:read` | Enabled flag, active preset (if any), and the requests-per-minute and burst being served. |
| `POST /api/v1/rate-limit/preset` | `features:toggle` | `{"preset": "strict"\|"normal"\|"relaxed"}` = 30/60/240 requests per minute with burst 5/10/60. Per-IP buckets are flushed so the new limits apply at once. Persisted. |
| `POST /api/v1/registry/scan` | `registry:write` | On-demand local autodiscovery: probes Ollama, LM Studio, vLLM and LiteLLM on the local host and `LLM_PROXY_DISCOVERY_PEERS`, and returns what it found. |

## Dashboards and analytics

| Route | Permission | Description |
|-------|-----------|-------------|
| `GET /api/v1/dashboard/summary` | `logs:read` | The attention list and suggested next steps behind the overview page: `now`, `attention`, `do_next`, `recent_changes`. If a section could not be built (for example the audit query failed) the rest is still returned and `section_errors` lists the missing sections: an empty `attention` is only "all clear" when `section_errors` is absent. |
| `GET /api/v1/slos` | `users:manage` | Per-endpoint error rates from the circuit breakers and the daily budget burn. |
| `GET /api/v1/analytics/forecast` | `users:manage` | Today's burn rate, projected total, headroom and time to the limit. |
| `GET /api/v1/analytics/cost-efficiency` | `users:manage` | Average cost per request and savings against a premium-model baseline. |
| `GET /api/v1/metrics/hourly-buckets` | `logs:read` | 24 hourly buckets of KPI counters and gauges. |
| `GET /api/v1/plugins/stats` | `registry:read` | Per-plugin invocation, error, timeout and latency counters. |
| `POST /api/v1/logs/token` | `logs:clear` | Mints a short-lived token (default 120 s) for the browser's log `EventSource`, which cannot send headers. |
| `POST /api/v1/logs/client` | `logs:clear` | Ingest of browser log records (batches up to 100) into the operator log view; `404` when `security.client_logs.enabled` is false. |
| `GET /api/v1/openapi.json` | `users:manage` | The OpenAPI schema, behind admin auth (FastAPI's own `/openapi.json` is disabled when authentication is on). |

## Telemetry Stream

```
GET /api/v1/telemetry/stream
```

Real-time SSE stream of system events (used by SOC dashboard).

```
GET /api/v1/logs
```

SSE log stream for terminal view.

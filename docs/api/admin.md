# API: Admin & Registry

Control and configuration endpoints for proxy management.

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

## Telemetry Stream

```
GET /api/v1/telemetry/stream
```

Real-time SSE stream of system events (used by SOC dashboard).

```
GET /api/v1/logs
```

SSE log stream for terminal view.

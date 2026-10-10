# Home (Threats)

The first screen of the admin UI. The sidebar entry is "Home" and the page title is "Threats". It shows counters, the daily budget, firewall statistics, ring latency and a feed of security events. The sections refresh every 10 seconds.

## Counters

Eight tiles, read from `/metrics` and `/health`. The counters come from the Prometheus metrics of the running process, so they start at zero when the process starts.

| Tile | What it shows |
|------|---------------|
| **Requests Today** | Sum of `llm_proxy_requests_total` since the process started. It is not reset at midnight |
| **Threats Blocked** | `llm_proxy_injection_blocked_total` plus `llm_proxy_auth_failures_total` |
| **PII Masked** | `llm_proxy_injection_blocked_total`. The proxy exports no count of masked PII; this tile repeats the injection counter |
| **Pass Rate** | `1 - blocked / requests`, with the two figures above |
| **Errors** | `llm_proxy_request_errors_total` |
| **Tokens** | `llm_proxy_token_usage_total` |
| **Uptime** | `uptime_seconds` from `/health` |
| **Healthy Endpoints** | `pool_healthy / pool_size` from `/health` |

The Requests, Threats Blocked and Errors tiles carry a sparkline from `/api/v1/metrics/hourly-buckets`.

## Other sections

| Section | Source |
|---------|--------|
| **Needs Attention** and **Do Next** | `/api/v1/dashboard/summary`: open circuit breakers, flagged callers, registry and budget conditions, each with a suggested action |
| **Spend forecast** | `/api/v1/analytics/forecast`: today's burn rate projected to the end of the day |
| **Daily Budget** | Spend today against `budget.daily_limit` |
| **ASGI Firewall** | Requests scanned and blocked, and blocks per signature, from `/api/v1/guards/status` |
| **Per-Endpoint Breakdown** | Requests and error rate per endpoint, from `/metrics` |
| **Ring Latency** | P50, P95 and P99 per plugin ring, from `/api/v1/metrics/latency` |
| **TTFT** | Time to first token of streamed responses, same source |
| **Ring Execution Timeline** | The last 20 request traces, from `/api/v1/metrics/ring-timeline` |
| **Security Pipeline** | A fixed diagram of the request path |

## Threat Timeline

A Chart.js bar chart with 24 hourly bars and two series, "Blocked" and "Passed".

The chart is filled in the browser from the events of the feed below, one count per event, in the bar of the event's hour. It starts empty each time the page is loaded and holds no history from the server. An event counts as "Blocked" when its level is `SECURITY` or its message contains `BLOCK`; every other event of the feed counts as "Passed".

## Recent Security Events

The feed reads the log stream `/api/v1/logs` (server-sent events) and keeps the last 50 entries that match one of these conditions:

- the level is `SECURITY`, `WARNING`, `ERROR` or `CRITICAL`
- the message contains one of the words `SHIELD`, `BLOCK`, `INJECT`, `PII`, `FIREWALL`, `AUTH`, `RATE`, `ZT`, `PANIC`, `BUDGET`

Each row shows the time, the level and the message as logged. A row may offer:

- **Investigate**, when the entry has a request id
- **Explain**, when the entry names a rule
- **Mute**, which hides entries of the same kind. The muted kinds are kept in the browser's `localStorage`

After more than five consecutive stream errors the feed stops and shows a Reconnect button.

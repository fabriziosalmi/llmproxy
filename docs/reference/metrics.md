# Metrics Reference

Prometheus metrics are exposed at `GET /metrics` on the main port. The route requires an admin key (or, while `LLM_PROXY_ADMIN_KEYS` is unset, an inference key).

## Available Metrics

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `llm_proxy_requests_total` | Counter | method, endpoint, http_status | Every HTTP request, including those a middleware refused. `endpoint` is the matched route template, or `unmatched` |
| `llm_proxy_request_errors_total` | Counter | endpoint, error_class | Requests answered with 4xx (`client_error`) or 5xx (`server_error`) |
| `llm_proxy_request_latency_seconds` | Histogram | endpoint | Request handling time. Buckets from 10 ms to 60 s |
| `llm_proxy_streaming_ttft_seconds` | Histogram | endpoint | Time to first token of a streamed response. Buckets from 10 ms to 5 s |
| `llm_proxy_ring_latency_seconds` | Histogram | ring | Execution time of one plugin ring |
| `llm_proxy_token_usage_total` | Counter | endpoint, role | Tokens, `role` = `prompt` or `completion` |
| `llm_proxy_cost_total` | Counter | endpoint, model | Estimated cost in USD. A model name the configuration does not know is counted as `other` |
| `llm_proxy_budget_consumed_usd` | Gauge | — | Spend counted against today's budget |
| `llm_proxy_budget_limit_usd` | Gauge | — | `budget.daily_limit` |
| `llm_proxy_endpoint_pool_size` | Gauge | status | Endpoints in the routing pool, `status` = `healthy` or `unhealthy`. Refreshed on each `/metrics`, `/health` and `/ready` request |
| `llm_proxy_circuit_open` | Gauge | endpoint | Circuit breaker state (0 = not open, 1 = open). Set when a breaker changes state |
| `llm_proxy_injection_blocked_total` | Counter | — | Requests refused by the SecurityShield, the negative cache or an Ingress-ring plugin. Byte-firewall blocks are not counted here |
| `llm_proxy_tool_policy_total` | Counter | decision | Tool calls the tool policy refused (`refused`) or would have refused in `log_only` mode (`would_refuse`) |
| `llm_proxy_auth_failures_total` | Counter | reason | Authentication and authorization failures on the data plane and the control plane |
| `llm_proxy_audit_persistence_total` | Counter | route, outcome | Audit-row write attempts, `outcome` = `ok` or `fail` |
| `llm_proxy_audit_backlog` | Gauge | — | Spend and audit writes accepted but not yet stored |
| `llm_proxy_stream_outcomes_total` | Counter | outcome | How streamed responses ended: `completed`, `blocked`, `upstream_error`, `client_disconnect` |
| `llm_proxy_plugin_events_total` | Counter | plugin, event | Plugin failures and refusals: `timeout`, `error`, `block`, `quarantine_skip`, `quarantine_block` |
| `llm_proxy_load_shed_total` | Counter | — | Requests refused by admission control. Also incremented when a pending state write is dropped because its queue is full |
| `llm_proxy_instance_count` | Gauge | — | Live instances registered in the same Redis; needs Redis. More than 1 means the daily budget is enforced by each instance separately |
| `llm_proxy_background_last_success_timestamp` | Gauge | loop | Unix time of the last successful iteration of a background loop |

A stream's 200 status is sent before its body, so `llm_proxy_requests_total` counts every started stream as a success. `llm_proxy_stream_outcomes_total` records how it ended.

## Scraping

### Prometheus Configuration

```yaml
# prometheus.yml
scrape_configs:
  - job_name: llmproxy
    scrape_interval: 15s
    metrics_path: /metrics
    authorization:
      type: Bearer
      credentials_file: /etc/prometheus/llmproxy-admin-key
    static_configs:
      - targets: ['localhost:8090']
```

A second, unauthenticated exporter can be enabled with `server.metrics.enabled: true` (port 9091, bound to `127.0.0.1` by default). It is off in the shipped `config.yaml`. It serves the same registry, but `llm_proxy_endpoint_pool_size` is refreshed only by requests to the main port.

### Grafana Dashboard

Example queries for panels:

- **Request Rate**: `rate(llm_proxy_requests_total[5m])`
- **Error Rate**: `rate(llm_proxy_request_errors_total[5m])`
- **P95 Latency**: `histogram_quantile(0.95, rate(llm_proxy_request_latency_seconds_bucket[5m]))`
- **Budget Consumed**: `llm_proxy_budget_consumed_usd`
- **Circuit Breakers**: `llm_proxy_circuit_open`
- **Injection Blocks**: `rate(llm_proxy_injection_blocked_total[5m])`
- **Stalled background loop**: `time() - llm_proxy_background_last_success_timestamp`

## Internal Metrics API

Additional metrics are available as JSON (not Prometheus format). Both routes require an admin key:

```bash
# Per-ring and per-plugin latency percentiles
curl http://localhost:8090/api/v1/metrics/latency \
  -H "Authorization: Bearer your-admin-key"

# The 20 most recent request traces with per-ring breakdown
curl http://localhost:8090/api/v1/metrics/ring-timeline \
  -H "Authorization: Bearer your-admin-key"
```

## OpenTelemetry

With `observability.tracing.enabled: true`, a tracer provider is set up at startup and spans are exported over OTLP (gRPC) when `otlp_endpoint` is set:

```yaml
observability:
  tracing:
    enabled: true
    service_name: "llmproxy"
    otlp_endpoint: "localhost:4317"
```

The connection is plaintext only when `otlp_endpoint` starts with `localhost:`, `127.0.0.1:` or `::1:`; any other endpoint is contacted over TLS. `console_export: true` also prints spans to the console.

The FastAPI application is instrumented whenever the OpenTelemetry packages are installed; they are part of `requirements.txt`. If they are not installed, tracing is skipped.

# Metrics Reference

Prometheus metrics exposed at `/metrics`.

## Available Metrics

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `llm_proxy_requests_total` | Counter | method, endpoint, status | Total requests |
| `llm_proxy_request_errors_total` | Counter | error_class | Failed requests (4xx/5xx) |
| `llm_proxy_request_latency_seconds` | Histogram | — | Latency with P50/P95/P99 buckets (10ms → 60s) |
| `llm_proxy_streaming_ttft_seconds` | Histogram | — | Time To First Token for streaming |
| `llm_proxy_token_usage_total` | Counter | endpoint, role | Token usage (prompt/completion) |
| `llm_proxy_cost_total` | Counter | endpoint, model | Estimated cost in USD |
| `llm_proxy_budget_consumed_usd` | Gauge | — | Current day budget consumption |
| `llm_proxy_circuit_open` | Gauge | endpoint | Circuit breaker state (0=closed, 1=open) |
| `llm_proxy_injection_blocked_total` | Counter | — | Injection attempts blocked |
| `llm_proxy_auth_failures_total` | Counter | reason | Authentication failures |

## Scraping

### Prometheus Configuration

```yaml
# prometheus.yml
scrape_configs:
  - job_name: llmproxy
    scrape_interval: 15s
    static_configs:
      - targets: ['localhost:8090']
    metrics_path: /metrics
```

### Grafana Dashboard

Key panels to set up:

- **Request Rate**: `rate(llm_proxy_requests_total[5m])`
- **Error Rate**: `rate(llm_proxy_request_errors_total[5m])`
- **P95 Latency**: `histogram_quantile(0.95, rate(llm_proxy_request_latency_seconds_bucket[5m]))`
- **Budget Consumed**: `llm_proxy_budget_consumed_usd`
- **Circuit Breakers**: `llm_proxy_circuit_open`
- **Injection Blocks**: `rate(llm_proxy_injection_blocked_total[5m])`

## Internal Metrics API

Additional metrics available via API (not Prometheus format):

```bash
# Per-ring and per-plugin latency percentiles
curl http://localhost:8090/api/v1/metrics/latency \
  -H "Authorization: Bearer your-key"

# Recent request traces with per-ring breakdown
curl http://localhost:8090/api/v1/metrics/ring-timeline \
  -H "Authorization: Bearer your-key"
```

## OpenTelemetry

When enabled, distributed traces are exported via OTLP:

```yaml
observability:
  tracing:
    enabled: true
    service_name: "llmproxy"
    otlp_endpoint: "http://jaeger:4317"
```

All routes are auto-instrumented. If `opentelemetry` is not installed, tracing becomes no-ops with zero overhead.

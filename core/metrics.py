"""
LLMPROXY — Prometheus Metrics (Session 7 Enhanced)

Exposes /metrics endpoint via prometheus_client with:
  - Request rate, errors, latency P50/P95/P99
  - Token usage & cost tracking
  - Budget consumption gauge
  - Pool health
  - TTFT histograms
"""

from prometheus_client import (
    Counter, Histogram, Gauge, Summary,
    start_http_server, generate_latest, CONTENT_TYPE_LATEST,
)
import time

# ─── Latency buckets tuned for LLM workloads (ms → seconds) ───
LATENCY_BUCKETS = (
    0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0
)

# ─── Core request metrics ───
REQUEST_COUNT = Counter(
    'llm_proxy_requests_total',
    'Total number of requests handled',
    ['method', 'endpoint', 'http_status']
)
REQUEST_ERRORS = Counter(
    'llm_proxy_request_errors_total',
    'Total failed requests (4xx/5xx)',
    ['endpoint', 'error_class']
)
REQUEST_LATENCY = Histogram(
    'llm_proxy_request_latency_seconds',
    'Latency of requests in seconds (P50/P95/P99 via buckets)',
    ['endpoint'],
    buckets=LATENCY_BUCKETS,
)

# ─── Infrastructure metrics ───
ACTIVE_AGENTS = Gauge('llm_proxy_active_agents', 'Number of currently running agents')
ENDPOINT_POOL_SIZE = Gauge('llm_proxy_endpoint_pool_size', 'Number of endpoints in pool', ['status'])
CIRCUIT_OPEN = Gauge('llm_proxy_circuit_open', 'Whether circuit breaker is open', ['endpoint'])

# ─── Token & cost metrics ───
TOKEN_USAGE = Counter('llm_proxy_token_usage_total', 'Total token usage', ['endpoint', 'role'])
ESTIMATED_COST = Counter('llm_proxy_cost_total', 'Estimated cost in USD', ['endpoint', 'model'])
ROI_METRIC = Gauge('llm_proxy_roi_efficiency', 'Estimated efficiency (Success / Cost)')
BUDGET_CONSUMED = Gauge('llm_proxy_budget_consumed_usd', 'Budget consumed this month')
BUDGET_LIMIT = Gauge('llm_proxy_budget_limit_usd', 'Monthly budget limit')

# ─── Streaming metrics ───
STREAMING_TTFT = Histogram(
    'llm_proxy_streaming_ttft_seconds',
    'Time To First Token for streams',
    ['endpoint'],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.2, 0.5, 1.0, 2.0, 5.0),
)

# ─── Security metrics ───
INJECTION_BLOCKED = Counter('llm_proxy_injection_blocked_total', 'Injection attempts blocked')
AUTH_FAILURES = Counter('llm_proxy_auth_failures_total', 'Authentication failures', ['reason'])


def start_metrics_server(port: int = 9091):
    """Start standalone Prometheus metrics HTTP server."""
    start_http_server(port)


def get_metrics_response():
    """Generate Prometheus metrics for inline /metrics endpoint."""
    return generate_latest(), CONTENT_TYPE_LATEST

class MetricsTracker:
    @staticmethod
    def track_request(method: str, endpoint: str, status: int, duration: float):
        REQUEST_COUNT.labels(method=method, endpoint=endpoint, http_status=status).inc()
        REQUEST_LATENCY.labels(endpoint=endpoint).observe(duration)
        # Track errors separately for alerting
        if status >= 400:
            error_class = "client_error" if status < 500 else "server_error"
            REQUEST_ERRORS.labels(endpoint=endpoint, error_class=error_class).inc()

    @staticmethod
    def set_pool_size(status: str, size: int):
        ENDPOINT_POOL_SIZE.labels(status=status).set(size)

    @staticmethod
    def track_usage(endpoint: str, model: str, prompt_tokens: int, completion_tokens: int, cost: float):
        TOKEN_USAGE.labels(endpoint=endpoint, role="prompt").inc(prompt_tokens)
        TOKEN_USAGE.labels(endpoint=endpoint, role="completion").inc(completion_tokens)
        ESTIMATED_COST.labels(endpoint=endpoint, model=model).inc(cost)

    @staticmethod
    def set_roi(value: float):
        ROI_METRIC.set(value)

    @staticmethod
    def track_ttft(endpoint: str, duration: float):
        STREAMING_TTFT.labels(endpoint=endpoint).observe(duration)

    @staticmethod
    def set_budget(consumed: float, limit: float):
        BUDGET_CONSUMED.set(consumed)
        BUDGET_LIMIT.set(limit)

    @staticmethod
    def track_injection_blocked():
        INJECTION_BLOCKED.inc()

    @staticmethod
    def track_auth_failure(reason: str):
        AUTH_FAILURES.labels(reason=reason).inc()

    @staticmethod
    def set_circuit_state(endpoint: str, is_open: bool):
        CIRCUIT_OPEN.labels(endpoint=endpoint).set(1 if is_open else 0)

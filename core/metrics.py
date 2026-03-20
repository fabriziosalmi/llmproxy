from prometheus_client import Counter, Histogram, Gauge, start_http_server
import time

# Metrics definitions
REQUEST_COUNT = Counter('llm_proxy_requests_total', 'Total number of requests handled', ['method', 'endpoint', 'http_status'])
REQUEST_LATENCY = Histogram('llm_proxy_request_latency_seconds', 'Latency of requests in seconds', ['endpoint'])
ACTIVE_AGENTS = Gauge('llm_proxy_active_agents', 'Number of currently running agents')
ENDPOINT_POOL_SIZE = Gauge('llm_proxy_endpoint_pool_size', 'Number of verified endpoints in the pool', ['status'])
TOKEN_USAGE = Counter('llm_proxy_token_usage_total', 'Total token usage', ['endpoint', 'role']) # role: prompt/completion
ESTIMATED_COST = Counter('llm_proxy_cost_total', 'Estimated cost in USD', ['endpoint', 'model'])
ROI_METRIC = Gauge('llm_proxy_roi_efficiency', 'Estimated efficiency (Success / Cost)')
STREAMING_TTFT = Histogram('llm_proxy_streaming_ttft_seconds', 'Time To First Token for streams', ['endpoint'])

def start_metrics_server(port: int = 9090):
    start_http_server(port)

class MetricsTracker:
    @staticmethod
    def track_request(method: str, endpoint: str, status: int, duration: float):
        REQUEST_COUNT.labels(method=method, endpoint=endpoint, http_status=status).inc()
        REQUEST_LATENCY.labels(endpoint=endpoint).observe(duration)

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

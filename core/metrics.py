from prometheus_client import Counter, Histogram, Gauge, start_http_server
import time

# Metrics definitions
REQUEST_COUNT = Counter('llm_proxy_requests_total', 'Total number of requests handled', ['method', 'endpoint', 'http_status'])
REQUEST_LATENCY = Histogram('llm_proxy_request_latency_seconds', 'Latency of requests in seconds', ['endpoint'])
ACTIVE_AGENTS = Gauge('llm_proxy_active_agents', 'Number of currently running agents')
ENDPOINT_POOL_SIZE = Gauge('llm_proxy_endpoint_pool_size', 'Number of verified endpoints in the pool', ['status'])

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
    def set_active_agents(count: int):
        ACTIVE_AGENTS.set(count)

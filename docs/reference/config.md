# Configuration Reference

Complete reference for `config.yaml`. All fields with their types, defaults, and descriptions.

## Server

```yaml
server:
  host: 0.0.0.0              # Bind address
  port: 8090                  # Listen port
  timeout: 30s                # Request timeout
  keep_alive: 60s             # Keep-alive duration
  tls:
    enabled: false            # Enable TLS
    cert_file: ""             # Path to TLS certificate
    key_file: ""              # Path to TLS private key
    min_version: "1.2"        # Minimum TLS version
  auth:
    enabled: true             # Require authentication. An ABSENT key also means
                              # true (core/auth_policy.py) — a security gateway
                              # that omits this must authenticate, not open.
                              # LLM_PROXY_DEV_MODE=1 overrides it, loudly.
    api_keys_env: "LLM_PROXY_API_KEYS"   # Inference keys — what /v1/* accepts
    admin_keys_env: "LLM_PROXY_ADMIN_KEYS"  # Control-plane keys — the ONLY keys
                              # /api/v1/* and /admin/* accept. When the named
                              # variable is unset the proxy falls back to the
                              # inference bag, so every client key can apply
                              # config, install plugins and purge the audit
                              # log. Startup warns; it does not refuse.
  total_timeout: null         # Overall ceiling on an upstream request, seconds.
                              # Default none, deliberately: a single value
                              # shared with sock_read truncates any completion
                              # whose generation runs longer, and the forwarder
                              # then retries the truncated request against the
                              # next provider — so the caller waits twice and
                              # two providers bill. sock_read (server.timeout)
                              # is the bound that matters. Set this only if you
                              # want a hard cap and accept that.
  metrics:
    enabled: false            # Enable the standalone Prometheus exporter
    port: 9091                # Metrics port
    bind: "127.0.0.1"         # Loopback by default. This listener is opened
                              # OUTSIDE the ASGI app, so no middleware guards
                              # it — not auth, not the rate limiter, not the
                              # firewall — while it serves the same registry
                              # that GET /metrics keeps behind the admin
                              # credential. Widen it only where the network
                              # restricts the port (a scraped pod), and prefer
                              # the authenticated /metrics on the main port.
  admin:
    enabled: true             # Enable admin API
    port: 8081                # Admin port
  vllm:
    enabled: false            # Enable local vLLM integration
    model_path: ""            # Local model path
    fallback_threshold: 0.1   # Budget threshold to fallback to local
  storage:
    type: "sqlite"            # Storage type (sqlite or postgres)
    dsn_env: "DATABASE_URL"   # Environment variable containing the database DSN
    dsn: ""                   # Fallback database DSN if env var is empty
```

## Security

```yaml
security:
  enabled: true               # Enable security pipeline
  max_payload_size_kb: 512    # Maximum request body size
  max_messages: 50            # Maximum messages per request
  max_nesting_depth: 64       # Deepest {/[ nesting a body may contain. Size
                              # alone is not enough: 100k nested arrays is
                              # ~200 KB, under the cap above, and made the JSON
                              # parser raise RecursionError out of the handler
                              # as an unhandled 500. 0 disables the check.
  link_sanitization:
    enabled: true             # Enable URL sanitization
    blocked_domains: []       # Domains to block
```

## Identity

```yaml
identity:
  enabled: false              # Enable SSO/JWT authentication
  default_role: "user"        # Default role for new users
  providers:                  # OIDC providers
    - name: google
      client_id_env: "OIDC_GOOGLE_CLIENT_ID"
    - name: microsoft
      client_id_env: "OIDC_MICROSOFT_CLIENT_ID"
    - name: apple
      client_id_env: "OIDC_APPLE_CLIENT_ID"
  role_mappings: {}           # email → role mappings
  session_ttl: 3600           # Session token TTL (seconds)
```

## Endpoints

```yaml
endpoints:
  <name>:
    provider: "<provider>"    # Provider adapter name
    base_url: "<url>"         # Provider API base URL
    api_key_env: "<env>"      # Environment variable for API key
    models: []                # Available models
    rate_limit:               # Optional rate limits
      rpm: 3500               # Requests per minute
      tpm: 60000              # Tokens per minute
```

## Fallback Chains

```yaml
fallback_chains:
  "<model>":                  # Primary model name
    - provider: "<provider>"  # Fallback provider
      model: "<model>"        # Fallback model
```

## Model Aliases

```yaml
model_aliases:
  "<alias>": "<real-model-id>"
```

## Model Groups

```yaml
model_groups:
  "<group-name>":
    strategy: "cheapest"      # cheapest, fastest, weighted, random
    models:
      - model: "<model>"
        provider: "<provider>"
        weight: 0.5           # For weighted strategy
```

## Rotation

```yaml
rotation:
  strategy: "round_robin"    # round_robin, weighted, least_used, random
  failover:
    enabled: true
    max_retries: 3
    retry_delay: 1s
    switch_on_status: [429, 500, 503]
```

## Logging

```yaml
logging:
  level: "info"              # debug, info, warning, error
  format: "json"             # json or text
  output: ""                 # Log file path (empty = stdout)
  audit_trail:
    enabled: true            # Enable persistent audit log
    mask_pii: true           # Mask PII in audit entries
```

## Caching

```yaml
caching:
  enabled: true
  db_path: "cache.db"        # SQLite cache database path
  ttl: 3600                  # Cache TTL (seconds)
  eviction_interval: 3600    # Eviction check interval
  negative_cache:
    maxsize: 50000           # Max negative cache entries
    ttl: 300                 # Negative cache TTL
  redis_socket_timeout: 2.0  # Seconds to wait for a Redis reply
  redis_connect_timeout: 2.0 # Seconds to wait for the Redis connection
```

`redis_socket_timeout` and `redis_connect_timeout` apply to every Redis client
in the proxy — the rate limiter, the circuit breakers and the shared
orchestrator client. Without them redis-py waits indefinitely, so a Redis that
is *slow* rather than down hangs the request path: the fallbacks to local
in-memory state are triggered by exceptions, and a hang raises nothing. Each of
these operations is a single Lua invocation or one `HGETALL`, so the two-second
default is already generous; raise it only on a heavily shared Redis. A value of
zero or below is ignored rather than honoured, because to redis-py it means
"wait forever". `LLM_PROXY_REDIS_TIMEOUT` sets both where no config file is in
reach.

## Observability

```yaml
observability:
  tracing:
    enabled: true
    service_name: "llmproxy"  # OpenTelemetry service name
    otlp_endpoint: null       # OTLP collector endpoint
    console_exporter: true    # Print traces to console
  sentry:
    dsn_env: "SENTRY_DSN"    # Sentry DSN environment variable
  export:
    enabled: false
    output_dir: "exports"     # JSONL export directory
    scrub_pii: true          # Remove PII from exports
    compress_on_rotate: true  # Gzip on daily rotation
```

## Webhooks

```yaml
webhooks:
  enabled: false
  endpoints:
    - name: "<name>"
      target: "<type>"        # slack, teams, discord, generic
      url_env: "<env>"        # Webhook URL environment variable
      events: []              # Event types to send
```

**Event types:** `circuit_open`, `budget_threshold`, `injection_blocked`, `endpoint_down`, `endpoint_recovered`, `auth_failure`, `panic_activated`

## Budget

```yaml
budget:
  daily_limit: 50.0          # Hard daily cap (USD)
  soft_limit: 40.0           # Warning threshold (USD)
  fallback_to_local_on_limit: true  # Use local LLM when exhausted
```

## Connection Pool

The ceiling on concurrent upstream work, and the only one — nothing above it
performs admission control, so requests beyond `max_connections` wait inside
aiohttp's connector rather than being refused.

```yaml
connection_pool:
  max_connections: 100        # Total simultaneous upstream connections
  max_per_host: 30            # Per-provider cap
  connect_timeout: 10         # Seconds to establish a connection
  keepalive_timeout: 30       # Seconds an idle connection is kept
  dns_cache_ttl: 300          # Seconds a resolved host is cached
```

## Admission Control

The ceiling on how many data-plane requests are in flight at once. Without it
the only bound was the connector's `max_connections`, and past that requests
waited in aiohttp's unbounded internal queue with no deadline — so overload
became latency and memory growth rather than a refusal a client could act on.
The rate limiter does not cover this: it is per-IP and per-key, so many
well-behaved callers can saturate the proxy without any of them being
throttled.

Applied to `/v1/*` only. Shedding an operator's config-apply because inference
is busy would be the wrong trade.

```yaml
admission:
  max_in_flight: 100          # Defaults to connection_pool.max_connections —
                              # admitting more than the connector can serve
                              # just moves the queue back into aiohttp
  queue_factor: 2.0           # Waiting room = max_in_flight × this
  max_queued: 200             # Or set it directly; beyond it, 503
  retry_after_s: 1            # Retry-After header on a shed request
```

Set `max_in_flight: 0` to disable. `llm_proxy_load_shed_total` counts refusals.

## Circuit Breaker

Per-endpoint failure isolation. Backed by Redis when `caching.redis_url` is
set — the state transition runs as a Lua script so the check-and-transition is
atomic across processes — and by in-process state otherwise.

```yaml
circuit_breaker:
  failure_threshold: 5        # Consecutive failures before opening
  recovery_timeout: 60        # Seconds open before admitting a probe
```

## Threat Ledger

Cross-request correlation of injection scores, keyed by client IP and by API
key prefix. An actor whose scores sum past the threshold within the window is
blocked. Note the nesting: this lives **under `security:`**, not at the top
level, because SecurityShield reads it from its own section.

```yaml
security:
  threat_ledger:
    enabled: true
    threshold: 3.0            # Summed score at which an actor is blocked
    window_seconds: 600       # How far back scores are counted
    min_events: 3             # Fewer events than this never block, whatever
                              # the sum — one bad request is not a pattern
    max_actors: 50000         # LRU bound on tracked actors
```

## GDPR

```yaml
gdpr:
  auto_purge: true            # Run the retention purge loop at all
  retention_days: 90          # Audit and spend rows older than this are purged
                              # once per day by retention_purge_loop
```

## Rate Limiting

```yaml
rate_limiting:
  enabled: true
  requests_per_minute: 60    # Global rate limit
```

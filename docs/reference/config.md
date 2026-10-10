# Configuration Reference

Reference for the `config.yaml` keys listed below, with their types, defaults and descriptions. A value shown is the one the code uses when the key is absent, unless the comment says otherwise. Keys that belong to one feature (plugins, tool policy details, link risk scoring) are documented on that feature's page. [Keys that no code reads](#keys-that-no-code-reads) lists entries of the shipped `config.yaml` that have no effect.

## Server

```yaml
server:
  host: 0.0.0.0              # Bind address
  port: 8090                  # Listen port
  timeout: 30s                # Longest silence on a streamed upstream
                              # response (between chunks).
  response_timeout: 600s      # Longest a non-streaming upstream may take to
                              # answer. It sends nothing until the completion
                              # is whole, so this bounds the generation. On
                              # expiry: 504, and no fallback to the next
                              # provider (the request was delivered and is
                              # probably being billed).
  keep_alive: 60s             # HTTP keep-alive timeout of the listener. 60s is
                              # the shipped value; 5 seconds when absent.
  shutdown_timeout: 30        # Seconds open connections get to finish after a
                              # stop signal.
  cors_origins: null          # List of allowed CORS origins. null: only
                              # http://localhost:<port> and
                              # http://127.0.0.1:<port>.
  tls:
    enabled: false            # Serve HTTPS. When true, cert_file and key_file
                              # must be set and loadable or the proxy does not
                              # start.
    cert_file: ""             # Path to TLS certificate
    key_file: ""              # Path to TLS private key
    min_version: "1.2"        # "1.2" or "1.3". The shipped config sets "1.3".
  auth:
    enabled: true             # Require authentication. An absent key also means
                              # true (core/auth_policy.py). LLM_PROXY_DEV_MODE=1
                              # overrides it and logs a warning.
    api_keys_env: "LLM_PROXY_API_KEYS"   # Inference keys — what /v1/* accepts
    admin_keys_env: "LLM_PROXY_ADMIN_KEYS"  # Control-plane keys — the only keys
                              # /api/v1/*, /admin/* and /metrics accept. When
                              # the named variable is unset the proxy falls
                              # back to the inference keys, so every client key
                              # can apply config, install plugins and purge the
                              # audit log. Startup warns; it does not refuse.
  total_timeout: null         # Overall ceiling on an upstream request, seconds.
                              # Default none: a ceiling on the whole operation
                              # cuts any completion whose generation runs
                              # longer. server.timeout (streams) and
                              # server.response_timeout (everything else) are
                              # the bounds that apply by default.
  metrics:
    enabled: false            # Enable the standalone Prometheus exporter
    port: 9091                # Metrics port
    bind: "127.0.0.1"         # Loopback by default. This listener is opened
                              # outside the ASGI app, so no middleware guards
                              # it — not auth, not the rate limiter, not the
                              # firewall — while it serves the same registry
                              # that GET /metrics keeps behind the admin
                              # credential. Widen it only where the network
                              # restricts the port (a scraped pod), and prefer
                              # the authenticated /metrics on the main port.
  storage:
    type: "sqlite"            # sqlite or postgres
    db_path: "data/endpoints.db"  # SQLite file. LLM_PROXY_DB_PATH overrides it.
    dsn_env: "DATABASE_URL"   # Postgres: environment variable holding the DSN
    dsn: "postgresql://postgres:postgres@localhost:5432/llmproxy"
                              # Postgres: DSN used when that variable is empty
```

There is no admin section: the admin API is served on the main port and is gated by the admin credential tier, not by a separate listener.

## Security

```yaml
security:
  enabled: true               # SecurityShield inspection of requests
  firewall:
    enabled: true             # Byte-level signature scan of request bodies.
                              # LLM_PROXY_FIREWALL_ENABLED overrides it. Read
                              # at startup.
    body_timeout_seconds: 30  # Seconds the whole request body may take to
                              # arrive; 408 beyond it.
  tool_policy:                # Which tools a response may call, and when
    enabled: false            # (see /security/tool-policy). Off by default.
    mode: enforce             # log_only: record what would be refused
    allow: ["*"]              # Tools callable at all (case-sensitive patterns)
    deny: []                  # Never, whatever allow says
    after_tool_result: null   # Tools callable in a turn that follows a tool
                              # result. null: no restriction. List the
                              # read-only tools to stop an indirect injection.
  max_payload_size_kb: 512    # Maximum request body size; 413 beyond it
  max_messages: 50            # Maximum messages per request
  max_nesting_depth: 64       # Deepest {/[ nesting a body may contain; 400
                              # beyond it. 0 disables the check. Applies even
                              # when the firewall is disabled.
  link_sanitization:
    enabled: true             # Enable URL sanitization
    blocked_domains: []       # Domains to block
  response_signing:
    secret: ""                # Signs non-streaming responses when set.
                              # LLM_PROXY_SIGNING_KEY is used when this is
                              # empty. Off when both are empty. Streams are
                              # never signed.
  confirm:
    signing_secret: ""        # Secret for config confirm tokens. Empty: a
                              # random per-process secret.
  sse:
    signing_secret: ""        # Secret for the live-log token. Empty: a random
                              # per-process secret.
    token_ttl_seconds: 120    # Lifetime of that token (10 to 600)
```

## Identity

```yaml
identity:
  enabled: false              # Enable SSO/JWT authentication
  default_role: "user"        # Role given to a user with no mapping
  providers:                  # OIDC providers. Default: none.
    - name: google
      client_id_env: "OIDC_GOOGLE_CLIENT_ID"
    - name: microsoft
      client_id_env: "OIDC_MICROSOFT_CLIENT_ID"
    - name: apple
      client_id_env: "OIDC_APPLE_CLIENT_ID"
  role_mappings: {}           # email → list of roles
  session_ttl: 3600           # Session token TTL (seconds)
```

`client_id_env` defaults to `OIDC_<NAME>_CLIENT_ID`. The three providers above are the ones listed in the shipped `config.yaml`.

## Endpoints

```yaml
endpoints:
  <name>:
    provider: "<provider>"    # Adapter name; the endpoint name when absent
    base_url: "<url>"         # Provider API base URL
    api_key_env: "<env>"      # Environment variable holding the API key
    auth_type: "bearer"       # "none" for a server that takes no key
    models: []                # Models this endpoint serves. An empty list
                              # makes it a candidate for any model that no
                              # other endpoint lists.
```

## Fallback Chains

```yaml
fallback_chains:
  "<model>":                  # Primary model name
    - provider: "<provider>"  # Endpoint id or provider name
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
    strategy: "random"        # cheapest, fastest, weighted, random
    models:
      - model: "<model>"
        provider: "<provider>"
        weight: 1.0           # For the weighted strategy
```

## Routing

```yaml
routing:
  cost_weight: 0.3            # 0.0 ignores model price when scoring
                              # endpoints, 1.0 weighs it fully. Overridden by
                              # a value set through
                              # POST /api/v1/routing/cost-weight.
```

## Discovery

```yaml
discovery:
  local_scan: true            # Probe local Ollama / LM Studio / vLLM /
                              # LiteLLM. LLM_PROXY_LOCAL_DISCOVERY overrides it.
  peers: []                   # Extra hosts ("host" or "host:port").
                              # LLM_PROXY_DISCOVERY_PEERS, when set, replaces it.
  scan_interval_s: 300        # Seconds between re-probes; 0 disables them
```

## Caching

```yaml
caching:
  enabled: true
  db_path: "data/cache.db"   # SQLite cache database path
  ttl: 3600                  # Cache TTL (seconds)
  eviction_interval: 3600    # Eviction check interval (seconds)
  negative_cache:
    maxsize: 50000           # Max negative cache entries
    ttl: 300                 # Negative cache TTL (seconds)
  redis_url: null            # Redis for circuit-breaker state and shared
                             # endpoint statistics. The REDIS_URL environment
                             # variable is used when this is absent.
  redis_socket_timeout: 2.0  # Seconds to wait for a Redis reply
  redis_connect_timeout: 2.0 # Seconds to wait for the Redis connection
```

`redis_socket_timeout` and `redis_connect_timeout` apply to every Redis client
in the proxy — the rate limiter, the circuit breakers and the shared
orchestrator client. A value of zero or below is ignored, because to redis-py
it means "wait forever". `LLM_PROXY_REDIS_TIMEOUT` sets both when the config
keys are absent.

## Observability

```yaml
observability:
  tracing:
    enabled: false            # Initialise OpenTelemetry tracing (and Sentry,
                              # see below). The shipped config sets true.
    service_name: "llmproxy"  # OpenTelemetry service name
    otlp_endpoint: null       # OTLP gRPC collector endpoint
    console_export: false     # Print spans to the console
  sentry:
    dsn_env: null             # Environment variable holding the Sentry DSN.
                              # The shipped config sets "SENTRY_DSN". Read
                              # only when tracing.enabled is true.
  export:
    enabled: false
    output_dir: "exports"     # JSONL export directory
    scrub_pii: true           # Remove PII from exports
    compress_on_rotate: true  # Compress on daily rotation
```

The connection to `otlp_endpoint` is plaintext only when the value starts with
`localhost:`, `127.0.0.1:` or `::1:`; any other endpoint is contacted over TLS.

## Webhooks

```yaml
webhooks:
  enabled: false
  endpoints:
    - name: "<name>"
      target: "generic"       # slack, teams, discord, generic, siem
      url_env: "<env>"        # Environment variable holding the webhook URL
      events: ["*"]           # Event types to send; "*" is all of them
      secret_env: "<env>"     # Optional: variable holding an HMAC signing secret
```

**Event types:** `circuit_open`, `budget_threshold`, `injection_blocked`, `endpoint_down`, `endpoint_recovered`, `auth_failure`, `panic_activated`. `endpoint_down` is defined but no code emits it.

A webhook URL that resolves to a private or reserved address is rejected and that endpoint is skipped.

## Budget

```yaml
budget:
  daily_limit: 50.0          # Hard daily cap (USD). A request that would reach
                             # it is refused with 402.
  soft_limit: 40.0           # Warning threshold (USD): budget_threshold
                             # webhook event. Must not exceed daily_limit.
```

## Connection Pool

Sizes the aiohttp connector used for upstream requests. Requests beyond
`max_connections` wait inside the connector; [admission control](#admission-control)
refuses excess requests before they get there.

```yaml
connection_pool:
  max_connections: 100        # Total simultaneous upstream connections
  max_per_host: 30            # Per-provider cap
  connect_timeout: 10         # Seconds to establish a connection
  keepalive_timeout: 30       # Seconds an idle connection is kept
  dns_cache_ttl: 300          # Seconds a resolved host is cached
```

## Admission Control

The ceiling on how many data-plane requests are in flight at once. Up to
`max_in_flight` requests are served; up to `max_queued` more wait for a slot;
beyond that a request is refused with 503 and a `Retry-After` header. The rate
limiter does not cover this: it is per key and per IP, so many callers that
each stay under their limit can still saturate the proxy.

Applied to `/v1/*` only.

```yaml
admission:
  max_in_flight: 100          # Defaults to connection_pool.max_connections
  queue_factor: 2.0           # Waiting room = max_in_flight × this
  max_queued: 200             # Or set it directly; beyond it, 503
  retry_after_s: 1            # Retry-After header on a shed request
```

Set `max_in_flight: 0` to disable. `llm_proxy_load_shed_total` counts refusals.

## Circuit Breaker

Per-endpoint failure isolation. Backed by Redis when `caching.redis_url` or the
`REDIS_URL` environment variable is set — the state transition runs as a Lua
script so the check-and-transition is atomic across processes — and by
in-process state otherwise. The shipped `docker-compose.yml` sets `REDIS_URL`.

```yaml
circuit_breaker:
  failure_threshold: 5        # Consecutive failures before opening
  recovery_timeout: 60        # Seconds open before admitting a probe
```

## Threat Ledger

Cross-request correlation of injection scores, keyed by client IP and by the
first eight characters of the session id (which is derived from the caller's
key). An actor whose scores sum past the threshold within the window is
blocked. The ledger is held in process memory. Note the nesting: this lives
**under `security:`**, not at the top level.

```yaml
security:
  threat_ledger:
    enabled: true
    threshold: 3.0            # Summed score at which an actor is blocked
    window_seconds: 600       # How far back scores are counted
    min_events: 3             # Fewer events than this never block, whatever
                              # the sum
    max_actors: 50000         # Bound on tracked actors
```

## GDPR

```yaml
gdpr:
  auto_purge: true            # Run the retention purge loop at all
  retention_days: 90          # Audit and spend rows older than this are purged
                              # once per day by retention_purge_loop
```

## Audit

```yaml
audit:
  head_log_interval_seconds: 3600   # Period of the AUDIT HEAD line in the
                                    # process log; 0 turns it off
```

## Quotas

```yaml
rbac:
  db_path: "data/rbac.db"     # SQLite file holding per-key quotas
```

## Rate Limiting

Off by default. The shipped `config.yaml` has no `rate_limiting` section.

```yaml
rate_limiting:
  enabled: false
  requests_per_minute: 60    # Sustained rate per bearer token, or per client
                             # IP when the request carries no token
  burst: 10                  # Extra capacity above the sustained rate
  exempt_paths: ["/health", "/ready", "/metrics"]
  redis_url: null            # Share buckets through Redis. Absent: buckets
                             # are held in process memory.
```

## Keys that no code reads

The shipped `config.yaml` contains these entries. Nothing under `core/`, `proxy/`, `store/`, `plugins/` or `main.py` reads them, so changing them has no effect:

- `server.vllm` (`enabled`, `model_path`, `fallback_threshold`)
- `rotation` (`strategy`, `failover.*`). Endpoint selection is described in the [configuration guide](/guide/configuration#endpoint-selection).
- `logging` (`level`, `format`, `output`, `audit_trail.*`). The application log is written at INFO to standard error in a fixed format, and to no file.
- `rate_limit` (`rpm`, `tpm`) under an endpoint
- `budget.fallback_to_local_on_limit`
- `observability.tracing.console_exporter`. The key the code reads is `console_export`.
- `local_llm`
- `chatops`

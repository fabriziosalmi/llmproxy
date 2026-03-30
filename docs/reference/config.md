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
    enabled: true             # Require API key authentication
    api_keys_env: "LLM_PROXY_API_KEYS"  # Env var with comma-separated keys
  metrics:
    enabled: false            # Enable dedicated metrics port
    port: 9091                # Metrics port
  admin:
    enabled: true             # Enable admin API
    port: 8081                # Admin port
  vllm:
    enabled: false            # Enable local vLLM integration
    model_path: ""            # Local model path
    fallback_threshold: 0.1   # Budget threshold to fallback to local
```

## Security

```yaml
security:
  enabled: true               # Enable security pipeline
  max_payload_size_kb: 512    # Maximum request body size
  max_messages: 50            # Maximum messages per request
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
```

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

## Rate Limiting

```yaml
rate_limiting:
  enabled: true
  requests_per_minute: 60    # Global rate limit
```

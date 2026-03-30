# Configuration

LLMProxy is configured via `config.yaml` in the project root. Changes can be hot-reloaded via the admin API without restarting.

## Server

```yaml
server:
  host: 0.0.0.0
  port: 8090
  timeout: 30s
  keep_alive: 60s
  tls:
    enabled: false
    cert_file: "/etc/llmproxy/certs/server.crt"
    key_file: "/etc/llmproxy/certs/server.key"
    min_version: "1.2"
  auth:
    enabled: true
    api_keys_env: "LLM_PROXY_API_KEYS"
```

## Endpoints

Each endpoint maps to an LLM provider with its adapter:

```yaml
endpoints:
  openai:
    provider: "openai"
    base_url: "https://api.openai.com/v1"
    api_key_env: "OPENAI_API_KEY"
    models: ["gpt-4o", "gpt-4o-mini", "text-embedding-3-small"]
    rate_limit: { rpm: 3500, tpm: 60000 }

  anthropic:
    provider: "anthropic"
    base_url: "https://api.anthropic.com/v1"
    api_key_env: "ANTHROPIC_API_KEY"
    models: ["claude-sonnet-4-20250514", "claude-haiku-4-5-20251001"]
    rate_limit: { rpm: 1000 }
```

See [Endpoints Reference](/reference/endpoints) for all 15 providers.

## Fallback Chains

When a provider is down, LLMProxy tries alternatives in order:

```yaml
fallback_chains:
  "gpt-4o":
    - provider: anthropic
      model: "claude-sonnet-4-20250514"
    - provider: google
      model: "gemini-2.5-pro"
```

## Model Aliases

Shorthand names that resolve to real model IDs:

```yaml
model_aliases:
  "gpt4": "gpt-4o"
  "claude": "claude-sonnet-4-20250514"
  "fast": "gpt-4o-mini"
  "best": "gpt-4o"
  "cheap": "gemini-2.0-flash"
```

## Model Groups

Pool models with a routing strategy:

```yaml
model_groups:
  "auto":
    strategy: "cheapest"  # cheapest, fastest, weighted, random
    models:
      - { model: "gpt-4o-mini", provider: "openai", weight: 0.5 }
      - { model: "gemini-2.5-flash", provider: "google", weight: 0.3 }
      - { model: "claude-haiku-4-5-20251001", provider: "anthropic", weight: 0.2 }
```

## Rotation Strategy

```yaml
rotation:
  strategy: "round_robin"  # weighted, least_used, random
  failover:
    enabled: true
    max_retries: 3
    retry_delay: 1s
    switch_on_status: [429, 500, 503]
```

## Budget

```yaml
budget:
  daily_limit: 50.0    # Hard cap per day (USD)
  soft_limit: 40.0     # Webhook warning threshold (USD)
  fallback_to_local_on_limit: true
```

Budget is persisted to SQLite and survives restarts.

## Security

```yaml
security:
  enabled: true
  max_payload_size_kb: 512
  max_messages: 50
  link_sanitization:
    enabled: true
    blocked_domains: ["malicious-site.com"]
```

## Rate Limiting

```yaml
rate_limiting:
  enabled: true
  requests_per_minute: 60
```

## Hot Reload

Reload config without restart:

```bash
curl -X POST http://localhost:8090/api/v1/admin/reload \
  -H "Authorization: Bearer your-admin-key"
```

For full configuration reference, see [Reference: Configuration](/reference/config).

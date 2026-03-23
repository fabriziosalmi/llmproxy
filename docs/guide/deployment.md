# Deployment

## Docker Compose

The recommended way to run LLMProxy in production:

```bash
# Copy env template
cp .env.example .env
# Edit .env with your API keys

# Start
docker compose up -d

# Check health
curl http://localhost:8090/health

# View logs
docker compose logs -f llmproxy
```

The `docker-compose.yml` includes:

- **Health check**: 30s interval, 3 retries, 15s start period
- **Volume mounts**: `llmproxy-data` for persistence, `config.yaml` and `plugins/` read-only
- **Resource limits**: 2GB memory limit, 512MB reservation
- **Ports**: 8090 (API) + 9091 (Prometheus)

## Docker Build

```bash
docker build -t llmproxy .
docker run -d \
  --name llmproxy \
  -p 8090:8090 \
  -v ./config.yaml:/app/config.yaml:ro \
  -v ./plugins:/app/plugins:ro \
  --env-file .env \
  llmproxy
```

## Environment Variables

All sensitive values are loaded via environment variables (with optional Infisical SDK):

| Variable | Description |
|----------|-------------|
| `LLM_PROXY_API_KEYS` | Comma-separated proxy API keys |
| `LLM_PROXY_MASTER_KEY` | Encryption master key |
| `LLM_PROXY_IDENTITY_SECRET` | Internal JWT signing key |
| `OPENAI_API_KEY` | OpenAI provider key |
| `ANTHROPIC_API_KEY` | Anthropic provider key |
| `GOOGLE_API_KEY` | Google AI provider key |
| `SENTRY_DSN` | Sentry error tracking |
| `SLACK_WEBHOOK_URL` | Slack webhook for alerts |

## CI/CD

### GitHub Actions

**CI** (`.github/workflows/ci.yml`) — runs on every push/PR:
- **Lint**: ruff check
- **Test**: pytest with plugin/WASM test suite
- **Syntax**: AST parse of all Python files

**Docker** (`.github/workflows/docker.yml`) — runs on version tags (`v*`):
- Builds Docker image
- Pushes to GitHub Container Registry (GHCR)
- Tags: semver, minor, commit SHA

## Observability Setup

### Prometheus

Metrics are exposed at `/metrics` (port 8090):

```yaml
# prometheus.yml
scrape_configs:
  - job_name: llmproxy
    static_configs:
      - targets: ['localhost:8090']
```

### Sentry

```bash
pip install sentry-sdk[fastapi]
```

Set `SENTRY_DSN` in your environment. LLMProxy auto-configures:
- FastAPI + aiohttp integrations
- PII filtering (`send_default_pii=False`)
- 10% transaction sampling, 5% profiling

### Webhooks

Configure alerts for Slack, Teams, Discord, or generic webhooks:

```yaml
webhooks:
  enabled: true
  endpoints:
    - name: slack-ops
      target: slack
      url_env: "SLACK_WEBHOOK_URL"
      events: ["circuit_open", "budget_threshold", "panic_activated"]
```

Event types: `circuit_open`, `budget_threshold`, `injection_blocked`, `endpoint_down`, `endpoint_recovered`, `auth_failure`, `panic_activated`.

## Health Checks

```bash
# Liveness/readiness
curl http://localhost:8090/health

# Detailed metrics
curl http://localhost:8090/metrics

# Guard status
curl http://localhost:8090/api/v1/guards/status \
  -H "Authorization: Bearer your-key"
```

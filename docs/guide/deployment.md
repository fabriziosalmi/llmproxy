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

## Kubernetes / Helm

For orchestrating LLMProxy in high-availability environments, package and install it via the provided Helm chart (which includes a bundled Redis sub-chart).

### Installation

1. Fetch and compile the chart dependencies:
```bash
helm dependency update charts/llmproxy
```

2. Deploy the chart to your Kubernetes cluster:
```bash
helm upgrade --install llmproxy charts/llmproxy \
  --namespace llmproxy --create-namespace \
  --set ingress.enabled=true \
  --set ingress.hosts[0].host="llmproxy.example.com"
```

### Key Values Configuration

Overridable parameters in `values.yaml`:
- `replicaCount`: Number of gateway pod instances. **Default `1`, and leave it
  there.** Budget accounting, rate-limit buckets, circuit-breaker verdicts and
  the multi-turn injection detector's session memory all live in process
  memory, so a second replica does not share them — it doubles every limit. A
  daily spend cap of $50 across 10 pods is a $500 cap, with nothing reporting
  it, and a conversation split across pods is scored independently by each,
  weakening injection detection.
- `autoscaling.enabled`: **Not supported.** The chart exposes it, but scaling
  out multiplies the per-process state described above rather than adding
  capacity. It becomes safe once that state moves behind shared storage —
  `core/rate_limiter.py` already has the Redis-backed pattern to follow.
- `config`: Raw string contents of `config.yaml` injected into the configuration ConfigMap.
- `secrets.inline`: Dictionary of inline credential variables (e.g. `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `LLM_PROXY_API_KEYS`) automatically mapped as secrets.
- `redis.enabled`: Provision a bundled Redis cache cluster (default: `true`).

## CI/CD

### GitHub Actions

**CI** (`.github/workflows/ci.yml`) — runs on every push/PR:
- **Lint**: ruff check
- **Test**: pytest with plugin/WASM test suite
- **Syntax**: AST parse of all Python files

**Docker** (`.github/workflows/docker.yml`) — runs on version tags (`v*`) and pushes:
- Builds Docker image
- Pushes to GitHub Container Registry (GHCR)
- Tags: semver, minor, commit SHA

**There is no CD.** Deployment is manual and deliberately so.

Wiring it up would mean giving GitHub Actions a credential with root on the
deployment host, and the script that performed the deploy built the image *on
that host* — so a compromise of the GitHub account would have been equivalent
to root on the machine, in exchange for automating something that happens
about once a month. That trade was declined.

The scripts that did it are kept outside this repository: they encode host
addresses, remote paths and systemd unit names for one particular deployment
rather than anything a reader needs. What is published instead is the image —
`ghcr.io/fabriziosalmi/llmproxy`, built by `docker.yml` with provenance and
SBOM attestations, tagged by semver and by commit SHA.

To run a release, pull the tag you want and start it with a volume mounted at
`/app/data`, which is where the database, audit log and spend ledger live:

```bash
docker run -d --name llmproxy \
  -p 8090:8090 \
  -v llmproxy-data:/app/data \
  --env-file /path/to/keys.env \
  ghcr.io/fabriziosalmi/llmproxy:1.33.0
```

Mounting that volume is not optional. Without it the database is written into
the container's writable layer and is discarded on every restart, taking the
endpoint registry, the persisted budget, the spend history and the
tamper-evident audit chain with it. That was a real defect, fixed in 1.33.0.

## Backups

`data/` holds the only state that cannot be reconstructed by hand: the endpoint
registry, `app_state` (including the persisted daily budget), the spend ledger,
the RBAC subjects, and the tamper-evident audit chain. `cache.db` beside it is
disposable.

```bash
python scripts/backup_db.py                    # data/endpoints.db -> data/backups/
python scripts/backup_db.py --keep 14          # retain the newest 14
python scripts/backup_db.py --verify-only FILE # check a backup is readable
```

It uses SQLite's backup API rather than copying the file, so it is safe to run
against a live proxy — a plain `cp` can capture a torn page or miss a WAL
segment, producing a file that opens and is subtly wrong. That is the worst
outcome for an audit chain, which would then verify as *broken* rather than as
absent. Each backup is integrity-checked immediately, written `0600`, and the
row counts are printed so you can see it holds what you expect.

Restoring is copying the file back into place:

```bash
systemctl stop llmproxy        # or: docker stop llmproxy
cp data/backups/endpoints.db.bak.<timestamp> data/endpoints.db
systemctl start llmproxy
```

The round trip is exercised in `tests/test_backup_db.py` — the backup is taken,
the live database is deleted, the backup is restored and the rows are asserted
to have survived. An untested restore is not a backup.

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

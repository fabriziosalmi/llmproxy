# Deployment

LLMProxy runs as a single instance. The daily budget total, per-session injection scoring, the kill switch and the feature toggles are held in the memory of one process; a second instance does not share them.

## Docker Compose

To run LLMProxy with the shipped `docker-compose.yml`:

```bash
# Copy env template
cp .env.example .env
# Edit .env: set LLM_PROXY_API_KEYS (required), LLM_PROXY_ADMIN_KEYS and your provider keys

# Start
docker compose up -d

# Check health
curl http://localhost:8090/health

# View logs
docker compose logs -f llmproxy
```

The proxy exits at startup while `LLM_PROXY_API_KEYS` is unset or still holds the `.env.example` placeholder.

The compose file builds the image from the working tree (`build: .`); it does not pull the published image. It includes:

- **Health check**: 30s interval, 10s timeout, 3 retries, 15s start period. It fails only when `/health` reports `"status": "down"`.
- **Volume mounts**: `llmproxy-data` at `/app/data` for persistence, `./config.yaml` read-only, `./plugins` read-only at `/app/plugins/bundled`, and `llmproxy-plugins` at `/app/plugins/installed`.
- **Resource limits**: 2GB memory limit, 512MB reservation.
- **Ports**: 8090 (API and admin UI) on all interfaces, and 9091 on the host's loopback for the standalone Prometheus exporter, which is disabled in the shipped `config.yaml`.
- **Redis**: a `redis:8-alpine` service without a password, published on the host's loopback, with `REDIS_URL` set on the proxy. The proxy uses it for circuit-breaker state and shared endpoint statistics.
- **Logging**: `json-file`, 10 MB per file, 3 files.

## Docker Build

```bash
docker build -t llmproxy .
docker run -d \
  --name llmproxy \
  -p 8090:8090 \
  -v llmproxy-data:/app/data \
  -v ./config.yaml:/app/config.yaml:ro \
  -v ./plugins:/app/plugins/bundled:ro \
  -v llmproxy-plugins:/app/plugins/installed \
  --env-file .env \
  llmproxy
```

What the volumes are for:

- **`/app/data`** holds the state that cannot be reconstructed — see
  [Backups](#backups) below. Without this mount it lives in the container's
  writable layer and is lost when the container is removed or recreated.
- **`/app/plugins/bundled`** read-only, **not** `/app/plugins`. Plugin install
  writes into `plugins/installed`, so mounting the whole tree read-only makes
  `POST /api/v1/plugins/install` fail.
- **`llmproxy-plugins`** is that writable directory.

The image keeps a second copy of `signatures.yaml`, `injection_corpus.yaml` and
`pricing.yaml` in `/app/defaults`. They are read from there when a volume
mounted at `/app/data` hides the copies shipped in `data/`.

Use a **named volume** rather than a host bind mount for `/app/data` unless you
chown it first: the container runs as the non-root user `llmproxy` (the Helm
chart runs it as uid 999), and a bind mount that user cannot write makes
startup fail when the database is opened.

## Environment Variables

Provider keys, and the startup check for `LLM_PROXY_API_KEYS`, are read from the process environment. The key bags at request time, the identity secret, webhook URLs and OIDC client ids are resolved through a helper that asks Infisical first when the `infisical-sdk` package is installed and configured; that package is not in `requirements.txt` and not in the published image.

| Variable | Description |
|----------|-------------|
| `LLM_PROXY_API_KEYS` | Inference Bearer keys, comma-separated — what `/v1/*` accepts. Required when auth is on; the process exits without it. |
| `LLM_PROXY_ADMIN_KEYS` | Control-plane Bearer keys — the only keys `/api/v1/*`, `/admin/*` and `/metrics` accept. **Unset means every inference key can apply configuration, install plugins and purge the audit log**; the proxy warns at startup but still boots. |
| `LLM_PROXY_DEV_MODE` | `1` disables authentication entirely and logs a warning. Local development only. |
| `LLM_PROXY_MASTER_KEY` | **Optional and currently unused** — nothing calls `SecretManager.encrypt`/`.decrypt`. Provider credentials are referenced by environment-variable name and are not written to disk. |
| `LLM_PROXY_IDENTITY_SECRET` | Signs and verifies the session tokens issued by `/api/v1/identity/exchange`, and keys the derivation of session ids. When unset, session ids use a random per-process secret and change at every restart. |
| `LLM_PROXY_AUDIT_KEY` | Seals audit rows with HMAC-SHA-256 (32+ characters). With it, someone who can write the database but not read this variable cannot alter or remove audit rows unnoticed, except by cutting off the newest rows, which only a head recorded elsewhere reveals. Once set it cannot be unset without `audit/verify` failing. Unset: SHA-256, which a database writer can recompute. |
| `LLM_PROXY_AUDIT_KEY_PREVIOUS` | Comma-separated retired audit keys, kept while rows sealed with them are retained. |
| `LLM_PROXY_SIGNING_KEY` | Enables signing of non-streaming responses. Unset (and `security.response_signing.secret` empty): responses are not signed. |
| `LLM_PROXY_FIREWALL_ENABLED` | `0` disables the byte firewall's signature scan; overrides `security.firewall.enabled`. Read at startup. |
| `LLM_PROXY_DB_PATH` | Path of the SQLite database; overrides `server.storage.db_path` (default `data/endpoints.db`). |
| `REDIS_URL` | Redis for circuit-breaker state and shared endpoint statistics, when `caching.redis_url` is not set. |
| `CONFIG_FILE` | Path of the config file (default `config.yaml`). |
| `OPENAI_API_KEY` | OpenAI provider key |
| `ANTHROPIC_API_KEY` | Anthropic provider key |
| `GOOGLE_API_KEY` | Google AI provider key |
| `SENTRY_DSN` | Sentry DSN. Read when `observability.tracing.enabled` is true; the variable name comes from `observability.sentry.dsn_env`. |
| `SLACK_WEBHOOK_URL` | Webhook URL of the `slack-ops` entry in the shipped `config.yaml`. Used when `webhooks.enabled` is true. |

Each provider key variable is the one named by that endpoint's `api_key_env`.

## Kubernetes / Helm

The repository contains a Helm chart in `charts/llmproxy`. It deploys one pod (a Deployment with the `Recreate` strategy), a Service, a ConfigMap holding `config.yaml`, a PersistentVolumeClaim for `/app/data`, and optionally a Secret, an Ingress and a HorizontalPodAutoscaler. Liveness probes `/health`; readiness probes `/ready`.

Three things about the chart as it stands:

- **It does not create a ServiceAccount.** With the default
  `serviceAccount.create: true` the Deployment names a ServiceAccount that no
  template creates, and the pod is not started. Install with
  `serviceAccount.create=false` (the pod then uses the namespace's `default`
  account), or create the account yourself.
- **The Redis subchart is not wired to the proxy.** A Bitnami Redis is
  installed when `redis.enabled` is true (the default), but the chart sets no
  `REDIS_URL`. Set `env.REDIS_URL` to use it, or set `redis.enabled=false`.
- **The chart's default `config` enables the standalone metrics exporter on
  `0.0.0.0:9091`**, which has no authentication, and the Service publishes that
  port inside the cluster. Restrict it with a NetworkPolicy, or disable
  `server.metrics` in `config` and scrape the authenticated `/metrics` on 8090.

The published image is built for `linux/amd64` only.

### Installation

1. Fetch the chart dependencies:
```bash
helm dependency update charts/llmproxy
```

2. Create a Secret with the keys. The proxy exits at startup without `LLM_PROXY_API_KEYS`:
```bash
kubectl create namespace llmproxy
kubectl -n llmproxy create secret generic llmproxy-keys --from-env-file=keys.env
```

3. Deploy the chart:
```bash
helm upgrade --install llmproxy charts/llmproxy \
  --namespace llmproxy \
  --set serviceAccount.create=false \
  --set secrets.existingSecret=llmproxy-keys \
  --set env.REDIS_URL=redis://llmproxy-redis-master:6379/0
```

`llmproxy-redis-master` is the name of the Redis Service for a release named `llmproxy`.

To expose it through an Ingress, set the host together with its path; setting only the host leaves the rule without a path:
```bash
  --set ingress.enabled=true \
  --set 'ingress.hosts[0].host=llmproxy.example.com' \
  --set 'ingress.hosts[0].paths[0].path=/' \
  --set 'ingress.hosts[0].paths[0].pathType=Prefix'
```

### Key Values Configuration

Overridable parameters in `values.yaml`:
- `replicaCount`: Number of gateway pods. **Default `1`, and leave it
  there.** The daily budget total, per-session injection scoring, the kill
  switch and the feature toggles live in process memory, so a second replica
  does not share them. Each pod enforces the full daily limit against its own
  total, and a conversation split across pods is scored independently by each.
- `autoscaling.enabled`: **Not supported**, for the same reason. The chart
  exposes it; leave it `false`.
- `config`: Raw string contents of `config.yaml` injected into the ConfigMap. A change to it restarts the pod on `helm upgrade`.
- `secrets.existingSecret`: Name of an existing Secret whose keys become environment variables.
- `secrets.inline`: Map of variables (e.g. `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `LLM_PROXY_API_KEYS`) from which the chart creates a Secret. Ignored when `existingSecret` is set.
- `env`: Map of plain environment variables for the pod.
- `redis.enabled`: Install the Bitnami Redis subchart (default: `true`). See above: the proxy does not use it unless `env.REDIS_URL` is set.
- `persistence.enabled`: Claim a PersistentVolume for `/app/data` (default:
  `true`, and leave it there). Without it the endpoint registry, budget, spend
  ledger and audit chain live on the pod's ephemeral filesystem and are lost on
  every restart, rescheduling and `helm upgrade`. Set it to `false` only for a
  throwaway evaluation. `persistence.size` (default `2Gi`),
  `persistence.storageClass` (`""` = cluster default, `"-"` = no dynamic
  provisioning) and `persistence.existingClaim` (bind a claim you already have,
  e.g. a restored snapshot) tune it.

## CI/CD

### GitHub Actions

**CI** (`.github/workflows/ci.yml`) — runs on pushes to `main`, on `v*` tags and on pull requests to `main`:
- **Workflow hardening**: every action reference is pinned to a commit SHA that exists
- **Lint**: `ruff check`
- **Type check**: mypy on `core/`, `proxy/`, `store/`, `plugins/`
- **Dependency audit**: `pip-audit` on `requirements.lock`, and a licence gate
- **Lockfile**: `requirements.lock` is in sync with `requirements.txt`
- **Secret scan**: gitleaks over the full history
- **Supply chain**: `scripts/verify_deps.py --strict` and a `.pth` file audit
- **Test**: pytest with branch coverage, failing under 73%, with Postgres and Redis service containers
- **Invariants**: the invariant, determinism and concurrency tests
- **Syntax**: `compileall` on `core/`, `proxy/`, `store/`, `plugins/`
- **Docker image size**: builds the image and fails above 500 MB

**Docker** (`.github/workflows/docker.yml`) — a reusable workflow. CI calls it on pushes to `main` and on `v*` tags, after the lint, type-check, audit, supply-chain, test, invariant and syntax jobs have passed. It can also be started manually.
- Builds the image and pushes it to GitHub Container Registry (GHCR) with provenance and SBOM attestations
- Tags: `X.Y.Z` and `X.Y` for a release tag, the short commit SHA, `latest`, and `main` for builds of the main branch
- Platform: `linux/amd64` only

**There is no CD.** No workflow in this repository deploys anything; deployment is manual.

What is published is the image, `ghcr.io/fabriziosalmi/llmproxy`. To run a
release, pull the tag you want and start it with a volume mounted at
`/app/data`, which is where the database, audit log and spend ledger live, and
an env file that sets at least `LLM_PROXY_API_KEYS`:

```bash
docker run -d --name llmproxy \
  -p 8090:8090 \
  -v llmproxy-data:/app/data \
  --env-file /path/to/keys.env \
  ghcr.io/fabriziosalmi/llmproxy:1.39.2
```

Without that volume the database is written into the container's writable
layer and is lost when the container is removed or recreated, taking the
endpoint registry, the persisted budget, the spend history and the audit chain
with it.

## Backups

`data/` holds the state that cannot be reconstructed by hand: the endpoint
registry, `app_state` (including the persisted daily budget), the spend ledger,
the RBAC subjects and the audit chain in `data/endpoints.db`, and the per-key
quotas in `data/rbac.db`. `data/cache.db` beside them is disposable.

`scripts/backup_db.py` captures `data/endpoints.db` only. Copy `data/rbac.db`
alongside it (per-key quotas and their consumed budget; set `rbac.db_path` to
move it). The script applies to the SQLite store; with `server.storage.type:
postgres`, back up the database with PostgreSQL's own tools.

Nothing in `data/` is encrypted by the proxy. A `data/.llmproxy_salt` file may
exist on an install that has been upgraded across releases; no code path reads
it at this release.

```bash
python scripts/backup_db.py                    # data/endpoints.db -> data/backups/
python scripts/backup_db.py --keep 14          # retain the newest 14 (default 7)
python scripts/backup_db.py --verify-only FILE # check a backup is readable
python scripts/backup_db.py --restore FILE     # put a backup in place (proxy stopped)
```

Paths are relative to the working directory. In a container the script is at
`/app/scripts/backup_db.py` (`docker exec llmproxy python scripts/backup_db.py`),
and the backup is written inside the data volume; copy it off the host.

It uses SQLite's backup API rather than copying the file, so it can run
against a live proxy — a plain `cp` can capture a torn page or miss a WAL
segment. Each backup is integrity-checked immediately, written `0600`, and the
row counts are printed so you can see it holds what you expect.

Restore with the script, with the proxy stopped:

```bash
systemctl stop llmproxy        # or: docker stop llmproxy
python scripts/backup_db.py --restore data/backups/endpoints.db.bak.<timestamp>
systemctl start llmproxy
```

Do not restore with a plain `cp`. The database runs in WAL mode, so
`endpoints.db` is only part of its state: a `-wal` file left beside the restored
copy is replayed on the next open, and the rows written after the backup come
back on top of it. `--restore` verifies the backup first, then moves the
old `endpoints.db`, `-wal` and `-shm` together into `data/backups/pre-restore.<timestamp>/`
and puts the backup in place. If the backup does not verify, nothing is touched.

The round trip is exercised in `tests/test_backup_db.py`, including a restore
over a database with an unflushed WAL.

Schedule it. `--verify-only` checks a file that already exists and takes no
backup, so it is not a schedule; the plain command below takes one, verifies it
immediately, and prunes. Its normal output goes to `/dev/null` so that cron
(`MAILTO`) mails you only when something is written to stderr, which is when it
fails:

```cron
# Daily 03:00: back up, keep the newest 14.
0 3 * * * cd /opt/llmproxy && python scripts/backup_db.py --keep 14 >/dev/null
```

The line above is run by `tests/test_backup_db.py`.

Host hygiene around the proxy: keep secret-adjacent files at `0600`
(`backup_db.py` and `install.sh` write theirs that way — do the same for any
`.env.bak.*` that `scripts/rotate_keys.sh` leaves, and delete those once the
rotation is confirmed). The process writes its log to standard error and
standard output and to no file; the `logging` section of `config.yaml` is not
read. Under Docker Compose the `json-file` driver caps the log at 10 MB x 3. `install.sh --local` redirects the output to
`.proxy.log`, which nothing rotates. If you redirect the log to a file, rotate
it, for example:

```conf
/var/log/llmproxy/*.log {
  daily rotate 14 compress delaycompress
  create 0600 llmproxy llmproxy
}
```

### What is hash-pinned

The Docker image installs from `requirements.lock` with `--require-hashes`; CI
runs `pip-audit` on that same lock file. `install.sh` and `make setup`
(the bare-metal path) install `requirements.txt` instead, unhashed and resolved
at install time. The lock is compiled for Linux and Python 3.12. If you run
bare-metal, you can resolve your own lock on your platform (`uv pip compile
requirements.txt --generate-hashes --output-file requirements.lock`) and
install it with `pip install --require-hashes -r`.

## Upgrading and rolling back

How to upgrade depends on how the proxy was started:

- Shipped `docker-compose.yml` (builds from source): check out the release tag,
  then `docker compose up -d --build`.
- Published image: pull the new tag and recreate the container with the same
  `/app/data` volume.
- Helm: `helm upgrade`.

Read the CHANGELOG entry for the target release first: where a release changes
stored state, it carries an **Upgrading** paragraph naming the procedure.

**Rolling back is redeploying the previous release and keeping the volume.**
What that means for the database:

- Migrations are declared once in `store/schema.py` for both SQLite and
  Postgres, applied at startup, and recorded in a `_migrations` table so a
  migration is not applied twice.
- No migration removes or renames a column. `001` and `003` add columns to
  `audit_log`. `002` adds range checks to `endpoints` (on SQLite by rebuilding
  the table with the same columns) and brings out-of-range values into range.
- The audit chain is the exception to a clean rollback. Per the CHANGELOG, the
  row format changed in 1.38.0 and the change is one-way: once 1.38.0 or later
  has started on a database, a release before 1.38.0 reports the chain as
  broken.

So:

```bash
# Compose (shipped file, built from source)
git checkout v<previous version>
docker compose up -d --build

# Published image: recreate the container from the previous tag,
# with the same llmproxy-data volume

# Kubernetes — the PVC is retained across a rollback
helm rollback llmproxy
```

Do **not** delete the PVC or the `llmproxy-data` volume to "clean up" a bad
upgrade. That destroys the audit chain and the spend ledger, which no rollback
restores; if the database itself is the problem, restore a backup instead —
see [Backups](#backups).

The floor is **1.33.0**: per the CHANGELOG, releases before it kept the
database outside `data/`, so rolling back past it changes where the proxy looks
for its state.

## Observability Setup

### Prometheus

Metrics are exposed at `/metrics` (port 8090). The route requires an admin key, so
the scrape has to send one:

```yaml
# prometheus.yml
scrape_configs:
  - job_name: llmproxy
    authorization:
      type: Bearer
      credentials_file: /etc/prometheus/llmproxy-admin-key
    static_configs:
      - targets: ['localhost:8090']
```

### Sentry

`sentry-sdk` is part of `requirements.txt` and of the image. Sentry is
initialised at startup when all of these hold: `observability.tracing.enabled`
is true (it is in the shipped `config.yaml`), `observability.sentry.dsn_env`
names a variable (`SENTRY_DSN` in the shipped file), and that variable is set.
It is configured with:
- FastAPI + aiohttp integrations
- `send_default_pii=False`
- 10% transaction sampling, 5% profiling
- `environment` fixed to `production`
- `HTTPException` events dropped before sending

### Webhooks

Configure alerts for Slack, Teams, Discord, a generic HTTP endpoint or a SIEM collector (targets `slack`, `teams`, `discord`, `generic`, `siem`):

```yaml
webhooks:
  enabled: true
  endpoints:
    - name: slack-ops
      target: slack
      url_env: "SLACK_WEBHOOK_URL"
      events: ["circuit_open", "budget_threshold", "panic_activated"]
```

Event types: `circuit_open`, `budget_threshold`, `injection_blocked`, `endpoint_down`, `endpoint_recovered`, `auth_failure`, `panic_activated`. `endpoint_down` is defined but no code emits it.

## Health Checks

```bash
# Liveness: always 200; the verdict ("ok", "degraded", "down") is in the body
curl http://localhost:8090/health

# Readiness: 503 when the verdict is "down", 200 otherwise
curl -i http://localhost:8090/ready

# Prometheus metrics (admin key)
curl -H "Authorization: Bearer your-admin-key" http://localhost:8090/metrics

# Guard status (admin key)
curl http://localhost:8090/api/v1/guards/status \
  -H "Authorization: Bearer your-admin-key"
```

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
  -v llmproxy-data:/app/data \
  -v ./config.yaml:/app/config.yaml:ro \
  -v ./plugins:/app/plugins/bundled:ro \
  -v llmproxy-plugins:/app/plugins/installed \
  --env-file .env \
  llmproxy
```

Three of those volumes are not optional, and this recipe used to have none of
them:

- **`/app/data`** holds the only state that cannot be reconstructed — see
  [Backups](#backups) below. Without this mount it lives in the container's
  writable layer and is destroyed by the next `docker rm`, image update or
  recreate. That is the loss recorded in the 1.33.0 changelog as a production
  incident, and this command reproduced it.
- **`/app/plugins/bundled`** read-only, **not** `/app/plugins`. Plugin install
  writes into `plugins/installed`, so mounting the whole tree read-only makes
  `POST /api/v1/plugins/install` fail on a read-only filesystem.
- **`llmproxy-plugins`** is that writable half. `docker-compose.yml` has had
  this split for some time; this section did not.

Use a **named volume** rather than a host bind mount for `/app/data` unless you
chown it first: the container runs as uid 999, and a root-owned bind mount
fails at startup with `sqlite3.OperationalError: unable to open database file`
rather than a message naming ownership.

## Environment Variables

All sensitive values are loaded via environment variables (with optional Infisical SDK):

| Variable | Description |
|----------|-------------|
| `LLM_PROXY_API_KEYS` | Inference Bearer keys — what `/v1/*` accepts. Required when auth is on. |
| `LLM_PROXY_ADMIN_KEYS` | Control-plane Bearer keys — the only keys `/api/v1/*` and `/admin/*` accept. **Unset means every inference key can apply configuration, install plugins and purge the audit log**; the proxy warns at startup but still boots. |
| `LLM_PROXY_DEV_MODE` | `1` disables authentication entirely, with a warning naming itself. Local development only. |
| `LLM_PROXY_MASTER_KEY` | At-rest encryption master key. **Optional and currently unused** — nothing calls `SecretManager.encrypt`/`.decrypt`, because provider credentials are referenced by environment-variable name and never written to disk. |
| `LLM_PROXY_IDENTITY_SECRET` | Internal JWT signing key |
| `LLM_PROXY_AUDIT_KEY` | Seals audit rows with HMAC-SHA-256 (32+ characters). With it, someone who can write the database but not read this variable cannot alter or remove audit rows unnoticed. Once set it cannot be unset without `audit/verify` failing. Unset: SHA-256, which a database writer can recompute. |
| `LLM_PROXY_AUDIT_KEY_PREVIOUS` | Comma-separated retired audit keys, kept while rows sealed with them are retained. |
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
- `persistence.enabled`: Claim a PersistentVolume for `/app/data` (default:
  `true`, and leave it there). Without it the endpoint registry, budget, spend
  ledger, audit chain and encryption salt live on the pod's ephemeral filesystem
  and are destroyed on every restart, rescheduling and `helm upgrade`. Set it to
  `false` only for a throwaway evaluation. `persistence.size` (default `2Gi`),
  `persistence.storageClass` (`""` = cluster default, `"-"` = no dynamic
  provisioning) and `persistence.existingClaim` (bind a claim you already have,
  e.g. a restored snapshot) tune it.

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
  ghcr.io/fabriziosalmi/llmproxy:1.39.1
```

Mounting that volume is not optional. Without it the database is written into
the container's writable layer and is discarded on every restart, taking the
endpoint registry, the persisted budget, the spend history and the
tamper-evident audit chain with it. That was a real defect, fixed in 1.33.0.

## Backups

`data/` holds the only state that cannot be reconstructed by hand: the endpoint
registry, `app_state` (including the persisted daily budget), the spend ledger,
the RBAC subjects, the tamper-evident audit chain, and `.llmproxy_salt`.
`cache.db` beside it is disposable.

**Back up the salt with the database.** `.llmproxy_salt` is one half of the key
that decrypts every stored credential; the database is the other half. Restoring
`endpoints.db` onto a host that lost the salt gives you back every row and no way
to read any encrypted value in it — and the proxy will not report that clearly,
because a value that fails to decrypt is returned as-is, so the symptom is 401s
from every provider. `scripts/backup_db.py` captures `data/endpoints.db` only; copy the
salt alongside it, with the same `0600` mode, and `data/rbac.db` (per-key quotas and
their consumed budget; from 1.37.22 it lives in `data/`, set `rbac.db_path` to move it).

If you are upgrading from a release where the salt sat in the working directory
(`/app/.llmproxy_salt` rather than `/app/data/.llmproxy_salt`), the proxy keeps
using the old file and logs a warning naming the new location. Move it while the
proxy is stopped — do not delete it and do not let a rebuild discard it.

```bash
python scripts/backup_db.py                    # data/endpoints.db -> data/backups/
python scripts/backup_db.py --keep 14          # retain the newest 14
python scripts/backup_db.py --verify-only FILE # check a backup is readable
python scripts/backup_db.py --restore FILE     # put a backup in place (proxy stopped)
```

It uses SQLite's backup API rather than copying the file, so it is safe to run
against a live proxy — a plain `cp` can capture a torn page or miss a WAL
segment, producing a file that opens and is subtly wrong. That is the worst
outcome for an audit chain, which would then verify as *broken* rather than as
absent. Each backup is integrity-checked immediately, written `0600`, and the
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
back on top of it. A 5-row backup copied over a database whose WAL held 500
later rows opened as 505. `--restore` verifies the backup first, then moves the
old `endpoints.db`, `-wal` and `-shm` together into `data/backups/pre-restore.<timestamp>/`
(as a set that still opens, in case the restore was the wrong call) and puts the
backup in place. If the backup does not verify, nothing is touched.

The round trip is exercised in `tests/test_backup_db.py`, including a restore
over a database with an unflushed WAL. An untested restore is not a backup.

Schedule it. `--verify-only` checks a file that already exists and takes no
backup, so it is not a schedule; the plain command below takes one, verifies it
immediately, and prunes. Its normal output goes to `/dev/null` so that cron
(`MAILTO`) mails you only when something is written to stderr, which is when it
fails:

```cron
# Daily 03:00: back up, keep the newest 14.
0 3 * * * cd /opt/llmproxy && python scripts/backup_db.py --keep 14 >/dev/null
```

The line above is run by `tests/test_backup_db.py`, so the documented command
cannot drift into one that does nothing.

Host hygiene around the proxy: secret-adjacent files must be `0600`
(`backup_db.py` already writes backups that way — extend the habit to `.env`,
any `temp_secrets*` and rotated `.env.bak.*`, and delete the `.bak` files once
the rotation is confirmed), and file log output needs rotation or it grows
without bound (a 30MB+ `.proxy.log` at `0644` in the workdir is the shape this
takes when nobody configures it). With `json-file` logging the compose file
already caps at `10m x3`; for bare-metal, a minimal logrotate:

```conf
/var/log/llmproxy/*.log {
  daily rotate 14 compress delaycompress
  create 0600 llmproxy llmproxy
}
```

### What is hash-pinned

The Docker image installs from `requirements.lock` with `--require-hashes`, so what
runs in the container is exactly what CI audited. `install.sh` and `make setup`
(the bare-metal path) install `requirements.txt` instead, unhashed and resolved
fresh, because the lock is compiled for Linux/Python 3.12 and does not install on
other platforms. If you run bare-metal in production, resolve your own lock on your
platform (`uv pip compile requirements.txt --generate-hashes --output-file
requirements.lock`) and install it with `pip install --require-hashes -r`.

## Upgrading and rolling back

Upgrade by moving the image tag (`docker compose pull && docker compose up -d`)
or by `helm upgrade`. Read the CHANGELOG entry for the target release first:
where a release moves state, it carries an **Upgrading** paragraph naming the
procedure — 1.34.0's chart PVC is the recent example.

**Rolling back is redeploying the previous tag and keeping the volume.** The
question that makes this non-obvious is whether an older binary can open a
database a newer one has migrated, and the answer is yes:

- Migrations are declared once in `store/schema.py` for both SQLite and
  Postgres, applied at startup, and recorded in a `_migrations` table so a
  migration is never applied twice.
- Every one of them is **additive** — new tables and new columns, never a drop
  or a rename. Readers either name their columns (`SELECT id, url, status, …
  FROM endpoints`) or select into a row mapping, so a column an older binary
  has never heard of becomes an unused key rather than an error.
- The `_migrations` table is likewise unknown to an older binary, which simply
  does not read it. Re-upgrading later re-applies nothing, because the newer
  binary finds its migrations already recorded.

So:

```bash
# Compose
docker compose down
# edit the image tag back to the previous release
docker compose up -d

# Kubernetes — the PVC is retained across a rollback
helm rollback llmproxy
```

Do **not** delete the PVC or the `llmproxy-data` volume to "clean up" a bad
upgrade. That destroys the audit chain and the spend ledger, which no rollback
restores; if the database itself is the problem, restore a backup instead —
see [Backups](#backups).

The floor is **1.33.0**: before it the store lived outside `data/` and the
encryption salt outside the volume, so rolling back past it moves where the
proxy looks for its own state.

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

# Detailed metrics (admin key)
curl -H "Authorization: Bearer $LLM_PROXY_ADMIN_KEY" http://localhost:8090/metrics

# Guard status
curl http://localhost:8090/api/v1/guards/status \
  -H "Authorization: Bearer your-key"
```

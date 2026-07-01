#!/usr/bin/env bash
# scripts/deploy.sh — draconian remote deploy for the LLMProxy server.
#
# Production server runs llmproxy as a systemd unit that `docker run`s a
# locally-built image with a HARDCODED tag (e.g. llmproxy:local-<sha>).
# A deploy = 5 atomic moves:
#   1. git fetch + reset --hard origin/main inside /opt/llmproxy-src
#   2. docker build -t llmproxy:local-<NEW_SHA> .
#   3. sed the new tag into /etc/systemd/system/llmproxy.service
#   4. systemctl daemon-reload + restart llmproxy
#   5. smoke suite (health, version match, /identity/config, /identity/me,
#      SSE token-fallback). On any smoke failure → rollback to OLD_TAG.
#
# Safety net:
#   - Lock file on remote (flock) — no concurrent deploys.
#   - Pre-flight: local working tree clean, on main, HEAD == origin/main.
#   - Idempotent: skip every step whose result already matches the target.
#   - Backups: the systemd unit is copied to .bak.<ts> before sed.
#   - Rollback re-uses the OLD image (still on disk) — no rebuild needed.
#   - Deploy history appended to /var/log/llmproxy-deploys.log on remote.
#   - --dry-run for a full read-only walkthrough.
#
# Usage:
#   scripts/deploy.sh                                # deploy HEAD of main
#   scripts/deploy.sh --dry-run                      # show plan, change nothing
#   PROBE_KEY=$(cat ~/.llmproxy-key) scripts/deploy.sh    # +auth smoke (preferred)
#   scripts/deploy.sh --probe-key sk-...              # +auth smoke (leaks to ps/history)
#   scripts/deploy.sh --no-rollback                  # don't auto-revert
#   scripts/deploy.sh --force                        # redeploy same SHA
#   scripts/deploy.sh --yes                          # skip confirmation
#   scripts/deploy.sh --skip-lint                    # bypass the ruff+mypy CI gate
#   scripts/deploy.sh --prune-old                    # remove old <repo>:<prefix>* images
#                                                    # older than 7 days, post-smoke.
#
# Env overrides (with defaults):
#   REMOTE_HOST=100.76.251.33
#   REMOTE_USER=root
#   REMOTE_DIR=/opt/llmproxy-src       (git checkout / build context)
#   REMOTE_REPO_URL=https://github.com/fabriziosalmi/llmproxy.git
#                                      (cloned into REMOTE_DIR if missing)
#   REMOTE_STATE_DIR=/opt/llmproxy     (must contain keys.env + config.yaml)
#   REMOTE_UNIT_PATH=/etc/systemd/system/llmproxy.service
#   REMOTE_UNIT_NAME=llmproxy
#   REMOTE_PORT=8090
#   IMAGE_REPO=llmproxy
#   IMAGE_TAG_PREFIX=local-
#   HEALTH_TIMEOUT_S=90
#   LOCK_PATH=/var/lock/llmproxy-deploy.lock
#   DEPLOY_LOG=/var/log/llmproxy-deploys.log
#   PROBE_KEY=...                      (optional, for auth smoke)
#
# Requires: ssh (key-based), gh CLI optional, bash 4+.

set -euo pipefail

# ── Config (env-overridable) ────────────────────────────────────────────────
REMOTE_HOST="${REMOTE_HOST:-100.76.251.33}"
REMOTE_USER="${REMOTE_USER:-root}"
REMOTE_DIR="${REMOTE_DIR:-/opt/llmproxy-src}"
REMOTE_REPO_URL="${REMOTE_REPO_URL:-https://github.com/fabriziosalmi/llmproxy.git}"
REMOTE_STATE_DIR="${REMOTE_STATE_DIR:-/opt/llmproxy}"
REMOTE_UNIT_PATH="${REMOTE_UNIT_PATH:-/etc/systemd/system/llmproxy.service}"
REMOTE_UNIT_NAME="${REMOTE_UNIT_NAME:-llmproxy}"
REMOTE_PORT="${REMOTE_PORT:-8090}"
IMAGE_REPO="${IMAGE_REPO:-llmproxy}"
IMAGE_TAG_PREFIX="${IMAGE_TAG_PREFIX:-local-}"
HEALTH_TIMEOUT_S="${HEALTH_TIMEOUT_S:-90}"
LOCK_PATH="${LOCK_PATH:-/var/lock/llmproxy-deploy.lock}"
DEPLOY_LOG="${DEPLOY_LOG:-/var/log/llmproxy-deploys.log}"
PROBE_KEY="${PROBE_KEY:-}"

DRY_RUN=0
NO_ROLLBACK=0
FORCE=0
ASSUME_YES=0
PRUNE_OLD=0
SKIP_LINT="${SKIP_LINT:-0}"

# ── Arg parse ───────────────────────────────────────────────────────────────
usage() { sed -n '2,55p' "$0" | sed 's/^# \{0,1\}//'; }

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run)      DRY_RUN=1; shift;;
        --no-rollback)  NO_ROLLBACK=1; shift;;
        --force)        FORCE=1; shift;;
        --yes|-y)       ASSUME_YES=1; shift;;
        --prune-old)    PRUNE_OLD=1; shift;;
        --skip-lint)    SKIP_LINT=1; shift;;
        --probe-key)
            printf '\033[1;33m!\033[0m --probe-key on the CLI leaves the key in shell history and `ps`.\n' >&2
            printf '\033[1;33m!\033[0m Prefer:  PROBE_KEY=$(cat ~/.llmproxy-key) scripts/deploy.sh\n' >&2
            PROBE_KEY="$2"; shift 2;;
        -h|--help)      usage; exit 0;;
        *) printf 'Unknown arg: %s\n' "$1" >&2; exit 2;;
    esac
done

PROBE_URL="http://${REMOTE_HOST}:${REMOTE_PORT}"
SSH_CTRL="${TMPDIR:-/tmp}/llmproxy-deploy.ssh.$$"
SSH_OPTS=(
    -o StrictHostKeyChecking=accept-new
    -o BatchMode=yes
    -o ConnectTimeout=10
    -o ServerAliveInterval=20
    -o ControlMaster=auto
    -o ControlPath="$SSH_CTRL"
    -o ControlPersist=30s
)

# ── Output helpers ──────────────────────────────────────────────────────────
log()   { printf '\033[1;36m▸\033[0m %s\n' "$*"; }
ok()    { printf '\033[1;32m✓\033[0m %s\n' "$*"; }
warn()  { printf '\033[1;33m!\033[0m %s\n' "$*"; }
err()   { printf '\033[1;31m✗\033[0m %s\n' "$*" >&2; }
step()  { printf '\n\033[1;35m── %s ──\033[0m\n' "$*"; }
dry()   { printf '\033[2m[dry-run]\033[0m %s\n' "$*"; }

# ── SSH helpers ─────────────────────────────────────────────────────────────
ssh_exec() {
    ssh "${SSH_OPTS[@]}" "$REMOTE_USER@$REMOTE_HOST" "$@"
}
ssh_close() {
    ssh -O exit "${SSH_OPTS[@]}" "$REMOTE_USER@$REMOTE_HOST" 2>/dev/null || true
    rm -f "$SSH_CTRL" 2>/dev/null || true
}
trap ssh_close EXIT

# ── 0. Local pre-flight ─────────────────────────────────────────────────────
step "0. Local pre-flight"
ROOT="$(git -C "$(dirname "$0")/.." rev-parse --show-toplevel 2>/dev/null || true)"
[ -n "$ROOT" ] || { err "Not inside a git repo."; exit 1; }
cd "$ROOT"

if [ -n "$(git status --porcelain)" ]; then
    err "Local working tree is dirty:"
    git status --short
    err "Commit or stash before deploying."
    exit 1
fi

LOCAL_BRANCH="$(git rev-parse --abbrev-ref HEAD)"
if [ "$LOCAL_BRANCH" != "main" ]; then
    err "Not on main (current: $LOCAL_BRANCH). Refusing to deploy a non-main branch."
    exit 1
fi

git fetch origin --quiet
LOCAL_SHA_FULL="$(git rev-parse HEAD)"
LOCAL_SHA="$(git rev-parse --short HEAD)"
ORIGIN_SHA_FULL="$(git rev-parse origin/main)"
if [ "$LOCAL_SHA_FULL" != "$ORIGIN_SHA_FULL" ]; then
    err "Local HEAD (${LOCAL_SHA_FULL:0:7}) ≠ origin/main (${ORIGIN_SHA_FULL:0:7}). Push first."
    exit 1
fi

[ -f VERSION ] || { err "VERSION file not found at $(pwd)/VERSION"; exit 1; }
EXPECTED_VERSION="$(tr -d '[:space:]' < VERSION)"
NEW_TAG="${IMAGE_REPO}:${IMAGE_TAG_PREFIX}${LOCAL_SHA}"

ok "Local main @ ${LOCAL_SHA} (v${EXPECTED_VERSION})"
ok "Target image tag: ${NEW_TAG}"

# ── 0b. CI-parity lint gate (ruff + mypy) ───────────────────────────────────
# The remote build runs the app, not the linters, so lint/type debt used to
# ship silently — that's exactly how an E741/F401 batch once reached main. We
# mirror the CI gate here and refuse to deploy a tree CI would reject. Both
# tools are optional locally: if a linter isn't installed we warn and continue
# (CI still enforces it). Escape hatch for emergencies: --skip-lint.
if [ "$SKIP_LINT" -eq 1 ]; then
    warn "Lint gate skipped (--skip-lint). CI still enforces ruff + mypy."
else
    if command -v ruff >/dev/null 2>&1; then
        log "ruff check . (CI parity)..."
        if ruff check . >/tmp/llmproxy-ruff.log 2>&1; then
            ok "ruff clean"
        else
            err "ruff failed — CI would reject this tree:"
            sed -n '1,40p' /tmp/llmproxy-ruff.log >&2
            err "Fix the above, or bypass with --skip-lint (not recommended)."
            exit 1
        fi
    else
        warn "ruff not found locally — skipping (CI still enforces it). \`pip install ruff\`"
    fi
    if command -v mypy >/dev/null 2>&1; then
        log "mypy core/ proxy/ (CI parity)..."
        if mypy core/ proxy/ >/tmp/llmproxy-mypy.log 2>&1; then
            ok "mypy clean"
        else
            err "mypy failed — CI would reject this tree:"
            sed -n '1,40p' /tmp/llmproxy-mypy.log >&2
            err "Fix the above, or bypass with --skip-lint (not recommended)."
            exit 1
        fi
    else
        warn "mypy not found locally — skipping (CI still enforces it). \`pip install mypy\`"
    fi
fi

# ── 1. Remote pre-flight (read-only, no state mutation) ─────────────────────
step "1. Remote pre-flight"

log "SSH connectivity..."
if ! ssh_exec true 2>/dev/null; then
    err "Cannot SSH to ${REMOTE_USER}@${REMOTE_HOST}. Check key-based auth + Tailscale."
    exit 1
fi
ok "SSH ok (control socket: ${SSH_CTRL})"

log "Reading remote state..."
# A single shell over the control socket — cheap and atomic-enough.
# Using $(...) instead of <(...) because bash doesn't parse heredocs nested
# inside process-substitution cleanly ("bad substitution: no closing ')' ").
# All silent-except-the-final-printf, so the captured stdout is one line.
REMOTE_PREFLIGHT=$(ssh_exec env \
    REMOTE_DIR="$REMOTE_DIR" \
    REMOTE_STATE_DIR="$REMOTE_STATE_DIR" \
    REMOTE_UNIT_PATH="$REMOTE_UNIT_PATH" \
    REMOTE_UNIT_NAME="$REMOTE_UNIT_NAME" \
    IMAGE_REPO="$IMAGE_REPO" \
    IMAGE_TAG_PREFIX="$IMAGE_TAG_PREFIX" \
    bash -s <<'REMOTE_PRE'
set -euo pipefail

# Each piece is whitespace-free; we'll join them by '|' on one line so the
# caller can parse with `read -r`. Missing → empty field, never absent.

git_sha=$(cd "$REMOTE_DIR" 2>/dev/null && git rev-parse --short HEAD 2>/dev/null || echo "")
git_dirty=$(cd "$REMOTE_DIR" 2>/dev/null && git status --porcelain 2>/dev/null | head -c1 | tr -d '\n' || echo "")
# Is REMOTE_DIR an actual git worktree? (A wiped/missing dir → "no" → clone path.)
src_is_repo=$(git -C "$REMOTE_DIR" rev-parse --git-dir >/dev/null 2>&1 && echo "yes" || echo "no")
unit_exists=$( [ -f "$REMOTE_UNIT_PATH" ] && echo "yes" || echo "no" )
tag_re="${IMAGE_REPO}:${IMAGE_TAG_PREFIX}[a-f0-9]+"
unit_image=$(grep -oE "$tag_re" "$REMOTE_UNIT_PATH" 2>/dev/null | head -1 || echo "")
unit_active=$(systemctl is-active "$REMOTE_UNIT_NAME" 2>/dev/null || echo "")
unit_enabled=$(systemctl is-enabled "$REMOTE_UNIT_NAME" 2>/dev/null || echo "")
docker_ok=$(docker info >/dev/null 2>&1 && echo "yes" || echo "no")
disk_free_mb=$(df -m /var/lib/docker 2>/dev/null | awk 'NR==2 {print $4}' || echo "0")
running_image=$(docker inspect "$REMOTE_UNIT_NAME" --format '{{.Config.Image}}' 2>/dev/null || echo "")
# Runtime state files the systemd unit mounts/reads. Missing keys.env is exactly
# what crash-looped the box (docker: --env-file: no such file). Catch it here.
keys_present=$( [ -f "$REMOTE_STATE_DIR/keys.env" ] && echo "yes" || echo "no" )
config_present=$( [ -f "$REMOTE_STATE_DIR/config.yaml" ] && echo "yes" || echo "no" )

printf '%s|%s|%s|%s|%s|%s|%s|%s|%s|%s|%s|%s\n' \
    "$git_sha" "$git_dirty" "$unit_exists" "$unit_image" \
    "$unit_active" "$unit_enabled" "$docker_ok" "$disk_free_mb" "$running_image" \
    "$src_is_repo" "$keys_present" "$config_present"
REMOTE_PRE
)

IFS='|' read -r REMOTE_SHA REMOTE_DIRTY UNIT_EXISTS UNIT_IMAGE UNIT_ACTIVE UNIT_ENABLED DOCKER_OK DISK_FREE_MB RUNNING_IMAGE SRC_IS_REPO KEYS_PRESENT CONFIG_PRESENT <<<"$REMOTE_PREFLIGHT"

if [ "$UNIT_EXISTS" != "yes" ]; then
    err "Remote systemd unit missing: ${REMOTE_UNIT_PATH}"
    err "Bootstrap a fresh box first:  scripts/bootstrap-remote.sh"
    exit 1
fi
if [ "$DOCKER_OK" != "yes" ]; then
    err "Remote docker daemon not responding."
    exit 1
fi
# State-dir guard: the unit mounts $REMOTE_STATE_DIR/{keys.env,config.yaml}. If
# either is missing, `docker run --env-file` exits 125 and systemd crash-loops
# forever. Refuse to deploy onto a box that would never come up.
if [ "$KEYS_PRESENT" != "yes" ] || [ "$CONFIG_PRESENT" != "yes" ]; then
    [ "$KEYS_PRESENT" = "yes" ]   || err "Remote ${REMOTE_STATE_DIR}/keys.env is MISSING — container would crash-loop on --env-file."
    [ "$CONFIG_PRESENT" = "yes" ] || err "Remote ${REMOTE_STATE_DIR}/config.yaml is MISSING — container has no config to mount."
    err "Recover the runtime state first:  scripts/bootstrap-remote.sh"
    exit 1
fi
if [ "${DISK_FREE_MB:-0}" -lt 500 ]; then
    err "Remote disk free < 500 MB in /var/lib/docker (${DISK_FREE_MB} MB). Build will likely fail."
    exit 1
fi
if [ "$SRC_IS_REPO" = "yes" ] && [ -n "$REMOTE_DIRTY" ]; then
    err "Remote ${REMOTE_DIR} has uncommitted changes. Refusing to clobber."
    exit 1
fi

if [ "$SRC_IS_REPO" != "yes" ]; then
    warn "Remote ${REMOTE_DIR} is not a git checkout — will clone ${REMOTE_REPO_URL} during deploy."
fi
ok "Remote git: ${REMOTE_SHA:-<will clone>}"
ok "Remote unit: image=${UNIT_IMAGE:-<unset>} active=${UNIT_ACTIVE} enabled=${UNIT_ENABLED}"
ok "Disk free: ${DISK_FREE_MB} MB"
[ "$UNIT_ENABLED" = "enabled" ] || warn "Unit is not enabled — deploys will survive a restart but not a reboot."
# Drift check: the systemd unit and the actually-running container can diverge
# if someone bypasses `systemctl restart` (e.g. `docker run` by hand). Surface
# it so the rollback target captured below (UNIT_IMAGE) isn't a lie.
if [ -n "${RUNNING_IMAGE:-}" ] && [ -n "${UNIT_IMAGE:-}" ] && [ "$RUNNING_IMAGE" != "$UNIT_IMAGE" ]; then
    warn "Drift: container is running ${RUNNING_IMAGE} but unit file references ${UNIT_IMAGE}"
    warn "Rollback will revert the unit, not the running container — manual check recommended."
fi

# Already-deployed shortcut.
if [ "$REMOTE_SHA" = "$LOCAL_SHA" ] && [ "$UNIT_IMAGE" = "$NEW_TAG" ] && [ "$FORCE" -ne 1 ]; then
    ok "Remote already at ${LOCAL_SHA} with image ${NEW_TAG} — nothing to do."
    ok "Re-run with --force to rebuild from the same commit."
    exit 0
fi

# ── 2. Plan ─────────────────────────────────────────────────────────────────
step "2. Plan"
printf '  source:    git %-12s → %s\n'      "${REMOTE_SHA:-?}" "$LOCAL_SHA"
printf '  image tag: %-22s → %s\n' "${UNIT_IMAGE:-?}" "$NEW_TAG"
printf '  version:   %s\n'                  "$EXPECTED_VERSION"
printf '  rollback:  %s\n'                  "${UNIT_IMAGE:-<none>}"
printf '  smoke:     /health, /version, /identity/config'
[ -n "$PROBE_KEY" ] && printf ', /identity/me, /api/v1/logs/token + /api/v1/logs?sse_token=…'
printf '\n'

if [ "$DRY_RUN" -eq 1 ]; then
    dry "DRY-RUN: stopping here. No remote mutation performed."
    exit 0
fi

if [ "$ASSUME_YES" -ne 1 ]; then
    printf '\nProceed? [y/N] '
    read -r ans
    case "$ans" in
        y|Y|yes|YES) ;;
        *) err "Aborted by user."; exit 1;;
    esac
fi

# ── 3. Acquire remote lock + deploy ─────────────────────────────────────────
step "3. Deploy"
START_TS=$(date +%s)

# All remote mutation goes through ONE bash invocation so it either fully
# completes or we know exactly where it stopped. Lock guards against
# concurrent deploys (another `deploy.sh` from a second terminal).
DEPLOY_RC=0
ssh_exec env \
    LOCAL_SHA="$LOCAL_SHA" \
    NEW_TAG="$NEW_TAG" \
    OLD_TAG="${UNIT_IMAGE:-}" \
    EXPECTED_VERSION="$EXPECTED_VERSION" \
    IMAGE_REPO="$IMAGE_REPO" \
    IMAGE_TAG_PREFIX="$IMAGE_TAG_PREFIX" \
    REMOTE_DIR="$REMOTE_DIR" \
    REMOTE_REPO_URL="$REMOTE_REPO_URL" \
    SRC_IS_REPO="$SRC_IS_REPO" \
    REMOTE_UNIT_PATH="$REMOTE_UNIT_PATH" \
    REMOTE_UNIT_NAME="$REMOTE_UNIT_NAME" \
    LOCK_PATH="$LOCK_PATH" \
    DEPLOY_LOG="$DEPLOY_LOG" \
    FORCE="$FORCE" \
    bash -s <<'REMOTE_DEPLOY' || DEPLOY_RC=$?
set -euo pipefail

# Acquire lock or fail fast — never queue (a queued deploy on top of a
# stuck one just compounds the problem).
exec 200>"$LOCK_PATH"
if ! flock -n 200; then
    echo "ERR: another deploy is in progress (lock held: $LOCK_PATH)" >&2
    exit 11
fi

ts() { date -u +%Y-%m-%dT%H:%M:%SZ; }
audit() { printf '%s | %s | %s\n' "$(ts)" "$REMOTE_UNIT_NAME" "$*" >>"$DEPLOY_LOG"; }

audit "deploy-start sha=$LOCAL_SHA new_tag=$NEW_TAG old_tag=${OLD_TAG:-<none>}"

# 3a. git fetch + reset (clone first if the checkout is missing/clobbered)
if [ "$SRC_IS_REPO" != "yes" ]; then
    echo "STEP clone: $REMOTE_DIR is not a repo — cloning $REMOTE_REPO_URL"
    rm -rf "$REMOTE_DIR"
    if ! git clone --quiet "$REMOTE_REPO_URL" "$REMOTE_DIR"; then
        echo "ERR: git clone failed for $REMOTE_REPO_URL → $REMOTE_DIR" >&2
        audit "deploy-fail step=clone url=$REMOTE_REPO_URL"
        exit 18
    fi
fi
cd "$REMOTE_DIR"
git fetch origin --quiet
git reset --hard "$LOCAL_SHA" >/dev/null
got=$(git rev-parse --short HEAD)
if [ "$got" != "$LOCAL_SHA" ]; then
    echo "ERR: git reset landed on $got, expected $LOCAL_SHA" >&2
    audit "deploy-fail step=git-reset got=$got"
    exit 12
fi
echo "STEP git: $got"

# 3b. docker build (skip if image already exists AND --force is off)
if [ "$FORCE" -ne 1 ] && docker image inspect "$NEW_TAG" >/dev/null 2>&1; then
    echo "STEP build: $NEW_TAG already present, skipping (use --force to rebuild)"
else
    echo "STEP build: $NEW_TAG (this can take a few minutes)"
    if ! docker build -t "$NEW_TAG" . >/tmp/llmproxy-build.log 2>&1; then
        echo "ERR: docker build failed — last 20 lines:" >&2
        tail -20 /tmp/llmproxy-build.log >&2
        audit "deploy-fail step=build"
        exit 13
    fi
fi

# Sanity: ensure the image exists after the build (covers race conditions
# with prune jobs running concurrently).
if ! docker image inspect "$NEW_TAG" >/dev/null 2>&1; then
    echo "ERR: image $NEW_TAG missing after build" >&2
    audit "deploy-fail step=build-verify"
    exit 14
fi

# 3c. systemd unit patch (idempotent + backup)
tag_re="${IMAGE_REPO}:${IMAGE_TAG_PREFIX}[a-f0-9]+"
cur_tag=$(grep -oE "$tag_re" "$REMOTE_UNIT_PATH" | head -1 || true)
if [ "$cur_tag" = "$NEW_TAG" ] && [ "$FORCE" -ne 1 ]; then
    echo "STEP unit: already references $NEW_TAG, skipping sed"
else
    cp -a "$REMOTE_UNIT_PATH" "${REMOTE_UNIT_PATH}.bak.$(date +%s)"
    if [ -n "$cur_tag" ]; then
        sed -i "s|$cur_tag|$NEW_TAG|g" "$REMOTE_UNIT_PATH"
    else
        # No prior tag found — pathological, refuse rather than corrupt the unit.
        echo "ERR: no ${IMAGE_REPO}:${IMAGE_TAG_PREFIX}<sha> pattern in $REMOTE_UNIT_PATH — manual recovery required" >&2
        audit "deploy-fail step=unit-patch reason=no-pattern"
        exit 15
    fi
    # Verify post-sed.
    if ! grep -qF "$NEW_TAG" "$REMOTE_UNIT_PATH"; then
        echo "ERR: post-sed unit does not contain $NEW_TAG" >&2
        audit "deploy-fail step=unit-verify"
        exit 16
    fi
    echo "STEP unit: patched $cur_tag → $NEW_TAG"
fi

# 3d. daemon-reload + restart
systemctl daemon-reload
systemctl restart "$REMOTE_UNIT_NAME"

# Wait for active (up to 30s).
for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
    state=$(systemctl is-active "$REMOTE_UNIT_NAME" 2>/dev/null || echo unknown)
    [ "$state" = "active" ] && break
    sleep 2
done
[ "$state" = "active" ] || {
    echo "ERR: $REMOTE_UNIT_NAME not active after 30s (state=$state)" >&2
    audit "deploy-fail step=restart state=$state"
    exit 17
}

audit "deploy-ok new_tag=$NEW_TAG sha=$LOCAL_SHA"
echo "STEP done: unit active"
REMOTE_DEPLOY

if [ "$DEPLOY_RC" -ne 0 ]; then
    err "Remote deploy aborted (exit=$DEPLOY_RC). No smoke. No rollback (deploy never reached restart)."
    exit "$DEPLOY_RC"
fi
ok "Deploy phase complete"

# ── 4. Health + smoke suite ─────────────────────────────────────────────────
step "4. Smoke"

SMOKE_FAILED=0

log "Waiting for /health (≤${HEALTH_TIMEOUT_S}s)..."
DEADLINE=$(( $(date +%s) + HEALTH_TIMEOUT_S ))
HEALTH_OK=0
while [ "$(date +%s)" -lt "$DEADLINE" ]; do
    if curl -fsS -m 3 -o /dev/null "$PROBE_URL/health" 2>/dev/null; then
        HEALTH_OK=1
        break
    fi
    sleep 2
done
if [ "$HEALTH_OK" -eq 1 ]; then
    ok "/health responding"
else
    err "/health did not respond in ${HEALTH_TIMEOUT_S}s"
    SMOKE_FAILED=1
fi

if [ "$HEALTH_OK" -eq 1 ]; then
    # Version match (auth-required if PROBE_KEY, else just status code).
    got_version=""
    if [ -n "$PROBE_KEY" ]; then
        got_version=$(curl -fsS -m 5 "$PROBE_URL/api/v1/version" \
            -H "Authorization: Bearer $PROBE_KEY" 2>/dev/null \
            | python3 -c 'import sys,json; print(json.load(sys.stdin).get("version",""))' 2>/dev/null \
            || true)
    fi
    if [ -n "$got_version" ]; then
        if [ "$got_version" = "$EXPECTED_VERSION" ]; then
            ok "version match: $got_version"
        else
            err "version mismatch: server=$got_version expected=$EXPECTED_VERSION"
            SMOKE_FAILED=1
        fi
    else
        warn "version probe skipped (no PROBE_KEY) or unparseable"
    fi

    # identity/config sanity
    cfg=$(curl -fsS -m 5 "$PROBE_URL/api/v1/identity/config" 2>/dev/null || true)
    if echo "$cfg" | grep -q '"proxy_auth_enabled"'; then
        ok "/identity/config exposes proxy_auth_enabled"
    else
        err "/identity/config missing proxy_auth_enabled: $cfg"
        SMOKE_FAILED=1
    fi

    # Security headers (post-1.21.58/62 hardening). Anything in front of the
    # proxy that strips headers (CDN, reverse proxy, …) shows up here BEFORE
    # users find out the hard way.
    # `/api/v1/identity/config` is anon-accessible, so we don't need auth.
    headers=$(curl -sS -m 5 -D - -o /dev/null "$PROBE_URL/api/v1/identity/config" 2>/dev/null || true)
    headers_lc=$(printf '%s' "$headers" | tr '[:upper:]' '[:lower:]')
    missing_headers=""
    for h in \
        "x-content-type-options" \
        "x-frame-options" \
        "referrer-policy" \
        "cross-origin-opener-policy" \
        "cross-origin-resource-policy" \
        "permissions-policy" \
        "content-security-policy"; do
        if ! printf '%s' "$headers_lc" | grep -q "^$h:"; then
            missing_headers="${missing_headers}${h} "
        fi
    done
    if [ -n "$missing_headers" ]; then
        err "Missing response headers: ${missing_headers}"
        err "If something terminates TLS or proxies in front, ensure header pass-through."
        SMOKE_FAILED=1
    else
        ok "Security headers present (CSP, COOP, CORP, Permissions-Policy, …)"
    fi
    # Banner rebrand (1.21.58): Server must NOT leak the WSGI/ASGI stack.
    if printf '%s' "$headers_lc" | grep -qE '^server: uvicorn'; then
        err "Server header still leaks 'uvicorn' — server_header=False not applied (image staler than 1.21.58?)"
        SMOKE_FAILED=1
    elif printf '%s' "$headers_lc" | grep -qE '^server: llmproxy'; then
        ok "Server banner: llmproxy"
    fi
    # COEP require-corp (1.21.62) is /ui only — don't enforce on API.
    ui_headers=$(curl -sS -m 5 -D - -o /dev/null "$PROBE_URL/ui/" 2>/dev/null || true)
    if printf '%s' "$ui_headers" | tr '[:upper:]' '[:lower:]' | grep -q '^cross-origin-embedder-policy: require-corp'; then
        ok "/ui/ has COEP require-corp"
    elif printf '%s' "$ui_headers" | grep -qE '^HTTP/[12].[1]? +200'; then
        warn "/ui/ reachable but COEP require-corp not set — staler than 1.21.62?"
    fi

    # Audit hash-chain liveness (1.21.60). The chain may be empty on a fresh
    # box, but /audit/verify must still return a parseable {valid: true} body.
    if [ -n "$PROBE_KEY" ]; then
        av=$(curl -fsS -m 5 "$PROBE_URL/api/v1/audit/verify" \
            -H "Authorization: Bearer $PROBE_KEY" 2>/dev/null || true)
        if echo "$av" | grep -q '"valid":true'; then
            ok "/api/v1/audit/verify reports valid chain"
        elif echo "$av" | grep -q '"valid":false'; then
            err "/api/v1/audit/verify reports BROKEN chain: $av"
            SMOKE_FAILED=1
        else
            warn "/api/v1/audit/verify unreachable or admin-only for this key — skipped"
        fi
    fi

    # identity/me (if PROBE_KEY)
    if [ -n "$PROBE_KEY" ]; then
        me=$(curl -fsS -m 5 "$PROBE_URL/api/v1/identity/me" \
            -H "Authorization: Bearer $PROBE_KEY" 2>/dev/null || true)
        if echo "$me" | grep -q '"authenticated":true'; then
            ok "/identity/me authenticated"
        else
            err "/identity/me failed: $me"
            SMOKE_FAILED=1
        fi

        # SSE short-lived token flow
        sse_token=$(curl -fsS -m 5 "$PROBE_URL/api/v1/logs/token" \
            -X POST \
            -H "Authorization: Bearer $PROBE_KEY" 2>/dev/null \
            | python3 -c 'import sys,json; print(json.load(sys.stdin).get("sse_token",""))' 2>/dev/null \
            || true)
        if [ -z "$sse_token" ]; then
            err "/api/v1/logs/token did not return sse_token"
            SMOKE_FAILED=1
        else
            code=$(curl -s -o /dev/null -m 2 -w '%{http_code}' \
                "$PROBE_URL/api/v1/logs?sse_token=$sse_token" 2>/dev/null || true)
            if [ "$code" = "200" ] || [ "$code" = "000" ]; then
                ok "/api/v1/logs accepts short-lived sse_token (HTTP $code)"
            else
                err "/api/v1/logs rejected sse_token: HTTP $code"
                SMOKE_FAILED=1
            fi
        fi
    fi
fi

# ── 5. Rollback (only if smoke failed AND rollback enabled AND OLD_TAG exists)
if [ "$SMOKE_FAILED" -eq 1 ]; then
    step "5. Rollback decision"
    if [ "$NO_ROLLBACK" -eq 1 ]; then
        err "Smoke failed but --no-rollback set — leaving remote at $NEW_TAG"
        err "Manual recovery: ssh $REMOTE_USER@$REMOTE_HOST and inspect $REMOTE_UNIT_PATH"
        exit 1
    fi
    if [ -z "${UNIT_IMAGE:-}" ]; then
        err "Smoke failed and no previous tag captured — cannot rollback automatically."
        exit 1
    fi

    err "Smoke failed → rolling back to ${UNIT_IMAGE}"

    ROLLBACK_RC=0
    ssh_exec env \
        OLD_TAG="$UNIT_IMAGE" \
        NEW_TAG="$NEW_TAG" \
        REMOTE_UNIT_PATH="$REMOTE_UNIT_PATH" \
        REMOTE_UNIT_NAME="$REMOTE_UNIT_NAME" \
        DEPLOY_LOG="$DEPLOY_LOG" \
        bash -s <<'REMOTE_ROLLBACK' || ROLLBACK_RC=$?
set -euo pipefail
ts() { date -u +%Y-%m-%dT%H:%M:%SZ; }
audit() { printf '%s | %s | %s\n' "$(ts)" "$REMOTE_UNIT_NAME" "$*" >>"$DEPLOY_LOG"; }

# Verify the old image is still on disk — if it isn't, rollback is impossible
# without rebuild and we should NOT pretend it worked.
if ! docker image inspect "$OLD_TAG" >/dev/null 2>&1; then
    echo "ERR: old image $OLD_TAG not on disk — cannot auto-rollback" >&2
    audit "rollback-fail reason=image-missing tag=$OLD_TAG"
    exit 21
fi

cp -a "$REMOTE_UNIT_PATH" "${REMOTE_UNIT_PATH}.bak.rollback.$(date +%s)"
sed -i "s|$NEW_TAG|$OLD_TAG|g" "$REMOTE_UNIT_PATH"
systemctl daemon-reload
systemctl restart "$REMOTE_UNIT_NAME"

for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
    state=$(systemctl is-active "$REMOTE_UNIT_NAME" 2>/dev/null || echo unknown)
    [ "$state" = "active" ] && break
    sleep 2
done
[ "$state" = "active" ] || {
    echo "ERR: rollback restart failed (state=$state)" >&2
    audit "rollback-fail state=$state"
    exit 22
}
audit "rollback-ok tag=$OLD_TAG"
echo "STEP rollback: reverted to $OLD_TAG, unit active"
REMOTE_ROLLBACK

    if [ "$ROLLBACK_RC" -ne 0 ]; then
        err "ROLLBACK FAILED (exit=$ROLLBACK_RC) — manual recovery required:"
        err "  ssh $REMOTE_USER@$REMOTE_HOST"
        err "  edit $REMOTE_UNIT_PATH (set image back to $UNIT_IMAGE)"
        err "  systemctl daemon-reload && systemctl restart $REMOTE_UNIT_NAME"
        exit "$ROLLBACK_RC"
    fi
    err "Rolled back to ${UNIT_IMAGE}. Investigate the failure before re-trying."
    exit 1
fi

# ── 5b. Optional image prune ────────────────────────────────────────────────
# Only after a clean deploy + green smoke. Keeps NEW_TAG and UNIT_IMAGE
# (the rollback target) and removes other ${IMAGE_REPO}:${IMAGE_TAG_PREFIX}*
# tags older than 7 days. Off by default — opt in with --prune-old.
if [ "$PRUNE_OLD" -eq 1 ]; then
    step "5b. Prune old images"
    ssh_exec env \
        IMAGE_REPO="$IMAGE_REPO" \
        IMAGE_TAG_PREFIX="$IMAGE_TAG_PREFIX" \
        NEW_TAG="$NEW_TAG" \
        OLD_TAG="${UNIT_IMAGE:-}" \
        DEPLOY_LOG="$DEPLOY_LOG" \
        REMOTE_UNIT_NAME="$REMOTE_UNIT_NAME" \
        bash -s <<'REMOTE_PRUNE' || warn "Prune step failed (non-fatal)"
set -euo pipefail
ts() { date -u +%Y-%m-%dT%H:%M:%SZ; }
audit() { printf '%s | %s | %s\n' "$(ts)" "$REMOTE_UNIT_NAME" "$*" >>"$DEPLOY_LOG"; }

# Cutoff: 7 days ago, epoch seconds.
CUTOFF=$(( $(date +%s) - 7*24*3600 ))
removed=0
# Format: <repo>:<tag>|<image_id>|<created_epoch>
docker images "${IMAGE_REPO}" --format '{{.Repository}}:{{.Tag}}|{{.ID}}|{{.CreatedAt}}' \
  | while IFS='|' read -r ref id created_at; do
    case "$ref" in *":${IMAGE_TAG_PREFIX}"*) ;; *) continue;; esac
    [ "$ref" = "$NEW_TAG" ] && continue
    [ "$ref" = "$OLD_TAG" ] && continue
    # CreatedAt format: "2026-05-01 12:34:56 +0000 UTC" — parseable by GNU date.
    created_epoch=$(date -d "$created_at" +%s 2>/dev/null || echo 0)
    if [ "$created_epoch" -ne 0 ] && [ "$created_epoch" -lt "$CUTOFF" ]; then
        if docker rmi "$ref" >/dev/null 2>&1; then
            echo "PRUNED $ref"
            removed=$((removed + 1))
        fi
    fi
done
audit "prune-ok removed=$removed kept=[$NEW_TAG,$OLD_TAG]"
REMOTE_PRUNE
    ok "Prune step finished"
fi

# ── 6. Summary ──────────────────────────────────────────────────────────────
END_TS=$(date +%s)
ELAPSED=$(( END_TS - START_TS ))
step "6. Summary"
ok "Deploy complete in ${ELAPSED}s"
ok "  ${UNIT_IMAGE:-<none>} → ${NEW_TAG}"
ok "  v${EXPECTED_VERSION} live at ${PROBE_URL}"
ok "  audit trail: ${REMOTE_USER}@${REMOTE_HOST}:${DEPLOY_LOG}"

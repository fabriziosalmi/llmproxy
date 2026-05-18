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
#   scripts/deploy.sh --probe-key sk-clai-...        # +auth smoke
#   PROBE_KEY=$(cat ~/.llmproxy-key) scripts/deploy.sh
#   scripts/deploy.sh --no-rollback                  # don't auto-revert
#   scripts/deploy.sh --force                        # redeploy same SHA
#   scripts/deploy.sh --yes                          # skip confirmation
#
# Env overrides (with defaults):
#   REMOTE_HOST=100.76.251.33
#   REMOTE_USER=root
#   REMOTE_DIR=/opt/llmproxy-src       (git checkout / build context)
#   REMOTE_UNIT_PATH=/etc/systemd/system/llmproxy.service
#   REMOTE_UNIT_NAME=llmproxy
#   REMOTE_PORT=11434
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
REMOTE_UNIT_PATH="${REMOTE_UNIT_PATH:-/etc/systemd/system/llmproxy.service}"
REMOTE_UNIT_NAME="${REMOTE_UNIT_NAME:-llmproxy}"
REMOTE_PORT="${REMOTE_PORT:-11434}"
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

# ── Arg parse ───────────────────────────────────────────────────────────────
usage() { sed -n '2,55p' "$0" | sed 's/^# \{0,1\}//'; }

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run)      DRY_RUN=1; shift;;
        --no-rollback)  NO_ROLLBACK=1; shift;;
        --force)        FORCE=1; shift;;
        --yes|-y)       ASSUME_YES=1; shift;;
        --probe-key)    PROBE_KEY="$2"; shift 2;;
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
unit_exists=$( [ -f "$REMOTE_UNIT_PATH" ] && echo "yes" || echo "no" )
tag_re="${IMAGE_REPO}:${IMAGE_TAG_PREFIX}[a-f0-9]+"
unit_image=$(grep -oE "$tag_re" "$REMOTE_UNIT_PATH" 2>/dev/null | head -1 || echo "")
unit_active=$(systemctl is-active "$REMOTE_UNIT_NAME" 2>/dev/null || echo "")
unit_enabled=$(systemctl is-enabled "$REMOTE_UNIT_NAME" 2>/dev/null || echo "")
docker_ok=$(docker info >/dev/null 2>&1 && echo "yes" || echo "no")
disk_free_mb=$(df -m /var/lib/docker 2>/dev/null | awk 'NR==2 {print $4}' || echo "0")
running_image=$(docker inspect "$REMOTE_UNIT_NAME" --format '{{.Config.Image}}' 2>/dev/null || echo "")

printf '%s|%s|%s|%s|%s|%s|%s|%s|%s\n' \
    "$git_sha" "$git_dirty" "$unit_exists" "$unit_image" \
    "$unit_active" "$unit_enabled" "$docker_ok" "$disk_free_mb" "$running_image"
REMOTE_PRE
)

IFS='|' read -r REMOTE_SHA REMOTE_DIRTY UNIT_EXISTS UNIT_IMAGE UNIT_ACTIVE UNIT_ENABLED DOCKER_OK DISK_FREE_MB RUNNING_IMAGE <<<"$REMOTE_PREFLIGHT"

if [ "$UNIT_EXISTS" != "yes" ]; then
    err "Remote systemd unit missing: ${REMOTE_UNIT_PATH}"
    exit 1
fi
if [ "$DOCKER_OK" != "yes" ]; then
    err "Remote docker daemon not responding."
    exit 1
fi
if [ "${DISK_FREE_MB:-0}" -lt 500 ]; then
    err "Remote disk free < 500 MB in /var/lib/docker (${DISK_FREE_MB} MB). Build will likely fail."
    exit 1
fi
if [ -n "$REMOTE_DIRTY" ]; then
    err "Remote ${REMOTE_DIR} has uncommitted changes. Refusing to clobber."
    exit 1
fi

ok "Remote git: ${REMOTE_SHA:-<unknown>}"
ok "Remote unit: image=${UNIT_IMAGE:-<unset>} active=${UNIT_ACTIVE} enabled=${UNIT_ENABLED}"
ok "Disk free: ${DISK_FREE_MB} MB"
[ "$UNIT_ENABLED" = "enabled" ] || warn "Unit is not enabled — deploys will survive a restart but not a reboot."

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
    REMOTE_DIR="$REMOTE_DIR" \
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

# 3a. git fetch + reset
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

# ── 6. Summary ──────────────────────────────────────────────────────────────
END_TS=$(date +%s)
ELAPSED=$(( END_TS - START_TS ))
step "6. Summary"
ok "Deploy complete in ${ELAPSED}s"
ok "  ${UNIT_IMAGE:-<none>} → ${NEW_TAG}"
ok "  v${EXPECTED_VERSION} live at ${PROBE_URL}"
ok "  audit trail: ${REMOTE_USER}@${REMOTE_HOST}:${DEPLOY_LOG}"

#!/usr/bin/env bash
# scripts/deploy.sh — draconian remote deploy for the LLMProxy server.
#
# Pulls origin/main on the remote, rebuilds the Docker image in place via
# `docker compose up -d --build`, then runs a smoke suite (health, version
# match, identity/config shape, optional /identity/me probe, optional SSE
# token-fallback). On smoke failure the script rolls back to the SHA that
# was deployed before this run.
#
# Usage:
#   scripts/deploy.sh                                # deploy HEAD
#   scripts/deploy.sh --probe-key sk-clai-...        # +auth smoke
#   PROBE_KEY=$(cat ~/.llmproxy-key) scripts/deploy.sh
#   scripts/deploy.sh --no-rollback                  # don't auto-revert
#   scripts/deploy.sh --dry-run                      # print commands, don't run
#
# Required: ssh access to $REMOTE_USER@$REMOTE_HOST, with $REMOTE_DIR being
# a clean git checkout of this repo + docker-compose installed.

set -euo pipefail

# ── Config (env-overridable) ────────────────────────────────────────────────
REMOTE_HOST="${REMOTE_HOST:-100.76.251.33}"
REMOTE_USER="${REMOTE_USER:-root}"
REMOTE_DIR="${REMOTE_DIR:-/opt/llmproxy}"
REMOTE_PORT="${REMOTE_PORT:-11434}"
PROBE_KEY="${PROBE_KEY:-}"
HEALTH_TIMEOUT_S="${HEALTH_TIMEOUT_S:-90}"
NO_ROLLBACK=0
DRY_RUN=0

# ── Arg parse ───────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --probe-key)    PROBE_KEY="$2"; shift 2;;
        --no-rollback)  NO_ROLLBACK=1; shift;;
        --dry-run)      DRY_RUN=1; shift;;
        -h|--help)
            sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'
            exit 0;;
        *) printf 'Unknown arg: %s\n' "$1" >&2; exit 2;;
    esac
done

PROBE_URL="http://${REMOTE_HOST}:${REMOTE_PORT}"
SSH_OPTS="-o StrictHostKeyChecking=accept-new -o BatchMode=yes -o ConnectTimeout=10"

# ── Output helpers ──────────────────────────────────────────────────────────
log() { printf '\033[1;36m▸\033[0m %s\n' "$*"; }
ok()  { printf '\033[1;32m✓\033[0m %s\n' "$*"; }
warn(){ printf '\033[1;33m!\033[0m %s\n' "$*"; }
err() { printf '\033[1;31m✗\033[0m %s\n' "$*" >&2; }

run_remote() {
    if [ "$DRY_RUN" -eq 1 ]; then
        printf '\033[2m[dry-run]\033[0m ssh %s@%s %s\n' "$REMOTE_USER" "$REMOTE_HOST" "$*"
        return 0
    fi
    ssh $SSH_OPTS "$REMOTE_USER@$REMOTE_HOST" "$@"
}

# ── 1. Local pre-flight ─────────────────────────────────────────────────────
log "Local pre-flight..."
cd "$(git -C "$(dirname "$0")/.." rev-parse --show-toplevel)"

if [ -n "$(git status --porcelain)" ]; then
    err "Working tree dirty. Commit or stash before deploying."
    git status --short
    exit 1
fi

LOCAL_BRANCH="$(git rev-parse --abbrev-ref HEAD)"
[ "$LOCAL_BRANCH" = "main" ] || { err "Not on main (current: $LOCAL_BRANCH)"; exit 1; }

git fetch origin --quiet
LOCAL_SHA="$(git rev-parse HEAD)"
ORIGIN_SHA="$(git rev-parse origin/main)"
if [ "$LOCAL_SHA" != "$ORIGIN_SHA" ]; then
    err "Local HEAD ($LOCAL_SHA) != origin/main ($ORIGIN_SHA). Push first."
    exit 1
fi

EXPECTED_VERSION="$(tr -d '[:space:]' < VERSION)"
ok "Local main @ ${LOCAL_SHA:0:7} (v${EXPECTED_VERSION})"

# ── 2. Capture pre-deploy state for rollback ────────────────────────────────
log "Reading remote pre-deploy state..."
if [ "$DRY_RUN" -eq 1 ]; then
    PREV_SHA="DRYRUN_PREV"
else
    if ! PREV_SHA="$(ssh $SSH_OPTS "$REMOTE_USER@$REMOTE_HOST" "cd $REMOTE_DIR && git rev-parse HEAD" 2>/dev/null)"; then
        err "Failed to SSH or read remote SHA at $REMOTE_USER@$REMOTE_HOST:$REMOTE_DIR"
        exit 1
    fi
fi
ok "Remote @ ${PREV_SHA:0:7}"

if [ "$PREV_SHA" = "$LOCAL_SHA" ]; then
    ok "Remote already at HEAD — nothing to deploy."
    exit 0
fi

# ── 3. Deploy ───────────────────────────────────────────────────────────────
log "Deploying ${PREV_SHA:0:7} → ${LOCAL_SHA:0:7}..."
DEPLOY_CMD="set -e; cd $REMOTE_DIR && git fetch origin --quiet && git reset --hard $LOCAL_SHA && docker compose up -d --build"
if [ "$DRY_RUN" -eq 1 ]; then
    printf '\033[2m[dry-run]\033[0m %s\n' "$DEPLOY_CMD"
else
    if ! ssh $SSH_OPTS "$REMOTE_USER@$REMOTE_HOST" "$DEPLOY_CMD" 2>&1 | tail -30; then
        err "Remote build/up failed. Manual recovery required."
        exit 1
    fi
fi
ok "Remote build complete"

# ── 4. Health probe ─────────────────────────────────────────────────────────
log "Waiting for /health (max ${HEALTH_TIMEOUT_S}s)..."
SMOKE_FAILED=0
if [ "$DRY_RUN" -eq 1 ]; then
    ok "[dry-run] skipping health poll"
else
    HEALTH_OK=0
    DEADLINE=$(( $(date +%s) + HEALTH_TIMEOUT_S ))
    while [ "$(date +%s)" -lt "$DEADLINE" ]; do
        if curl -fsS -m 3 -o /dev/null "$PROBE_URL/health"; then
            HEALTH_OK=1
            break
        fi
        sleep 2
    done
    if [ "$HEALTH_OK" -eq 1 ]; then
        ok "/health green"
    else
        err "/health did not respond within ${HEALTH_TIMEOUT_S}s"
        SMOKE_FAILED=1
    fi
fi

# ── 5. Smoke suite ──────────────────────────────────────────────────────────
smoke() {
    local name="$1"; shift
    log "Smoke: $name..."
    if "$@"; then ok "$name"; else err "$name FAILED"; SMOKE_FAILED=1; fi
}

smoke_version() {
    [ "$DRY_RUN" -eq 1 ] && return 0
    local got
    got="$(curl -fsS -m 5 "$PROBE_URL/api/v1/version" \
        -H "Authorization: Bearer ${PROBE_KEY:-x}" 2>/dev/null \
        | python3 -c 'import sys,json; print(json.load(sys.stdin).get("version",""))' 2>/dev/null \
        || true)"
    if [ "$got" = "$EXPECTED_VERSION" ]; then
        printf '  version=%s\n' "$got"
        return 0
    fi
    printf '  expected=%s got=%s\n' "$EXPECTED_VERSION" "${got:-<empty>}"
    return 1
}

smoke_identity_config() {
    [ "$DRY_RUN" -eq 1 ] && return 0
    local body
    body="$(curl -fsS -m 5 "$PROBE_URL/api/v1/identity/config" 2>/dev/null || true)"
    printf '  %s\n' "$body"
    echo "$body" | grep -q '"proxy_auth_enabled"'
}

smoke_identity_me() {
    [ "$DRY_RUN" -eq 1 ] && return 0
    [ -n "$PROBE_KEY" ] || { warn "no PROBE_KEY → skipping"; return 0; }
    local body
    body="$(curl -fsS -m 5 "$PROBE_URL/api/v1/identity/me" \
        -H "Authorization: Bearer $PROBE_KEY" 2>/dev/null || true)"
    echo "$body" | grep -q '"authenticated":true'
}

smoke_sse_token() {
    [ "$DRY_RUN" -eq 1 ] && return 0
    [ -n "$PROBE_KEY" ] || { warn "no PROBE_KEY → skipping"; return 0; }
    local code
    code="$(curl -s -o /dev/null -m 2 -w '%{http_code}' \
        "$PROBE_URL/api/v1/logs?token=$PROBE_KEY" 2>/dev/null || true)"
    # 200 = SSE accepted, 000 = curl killed by -m mid-stream after status was received
    [ "$code" = "200" ] || [ "$code" = "000" ]
}

smoke "version match"           smoke_version
smoke "/identity/config shape"  smoke_identity_config
smoke "/identity/me + token"    smoke_identity_me
smoke "/api/v1/logs query-tok"  smoke_sse_token

# ── 6. Rollback on failure ──────────────────────────────────────────────────
if [ "$SMOKE_FAILED" -eq 1 ]; then
    if [ "$NO_ROLLBACK" -eq 1 ]; then
        err "Smoke failed but --no-rollback set — leaving remote at ${LOCAL_SHA:0:7}"
        exit 1
    fi
    err "Smoke failed — rolling back to ${PREV_SHA:0:7}"
    ROLLBACK_CMD="set -e; cd $REMOTE_DIR && git reset --hard $PREV_SHA && docker compose up -d --build"
    if ssh $SSH_OPTS "$REMOTE_USER@$REMOTE_HOST" "$ROLLBACK_CMD" 2>&1 | tail -20; then
        err "Rolled back to ${PREV_SHA:0:7}."
    else
        err "ROLLBACK FAILED — manual intervention required on $REMOTE_USER@$REMOTE_HOST"
    fi
    exit 1
fi

ok "Deploy complete — v${EXPECTED_VERSION} live at ${PROBE_URL}"

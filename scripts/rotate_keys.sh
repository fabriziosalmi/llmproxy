#!/usr/bin/env bash
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# LLMProxy — Secret Rotation Script
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#
# Rotates all proxy-internal secrets atomically:
#   • LLM_PROXY_MASTER_KEY      (256-bit, Fernet KDF root)
#   • LLM_PROXY_IDENTITY_SECRET (256-bit, JWT/ZT signing)
#   • LLM_PROXY_FEDERATION_SECRET (256-bit, peer auth)
#   • LLM_PROXY_API_KEYS        (128-bit, client auth token)
#
# Usage:
#   ./scripts/rotate_secrets.sh                # interactive (confirm before write)
#   ./scripts/rotate_secrets.sh --apply        # non-interactive (CI/cron safe)
#   ./scripts/rotate_secrets.sh --dry-run      # preview only, no writes
#   ./scripts/rotate_secrets.sh --env /path    # custom .env location
#   ./scripts/rotate_secrets.sh --retire-old   # drop the previous client keys
#
# Client-key rotation is TWO steps by default. The first run prepends the new
# key and keeps the old ones valid, so consumers can move without an outage;
# --retire-old on a later run drops everything but the newest. Overwriting the
# bag in one step — which is what this used to do — makes rotation a hard
# cutover for every SDK consumer at the moment of restart.
#
# Safety:
#   • Atomic write via tmp+mv (no partial .env on crash)
#   • Timestamped backup before overwrite (.env.bak.<epoch>)
#   • PBKDF2 salt file detection — warns about re-encryption need
#   • Validates entropy source before generation
#   • Never prints full secrets to stdout (shows prefix + suffix only)
#
# Post-rotation checklist (printed at end):
#   1. Restart the proxy to pick up new keys
#   2. If MASTER_KEY changed, delete .llmproxy_salt to force salt regen
#      (existing Fernet-encrypted values become undecryptable — expected)
#   3. Distribute the new LLM_PROXY_API_KEYS entry to SDK consumers, then
#      re-run with --retire-old once they have all moved
#   4. If using Infisical, update the vault values instead of .env
#
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

set -euo pipefail

# ── Colors & formatting ─────────────────────────────────────────────────
readonly RED='\033[0;31m'
readonly GRN='\033[0;32m'
readonly YLW='\033[0;33m'
readonly BLU='\033[0;34m'
readonly CYN='\033[0;36m'
readonly DIM='\033[2m'
readonly BLD='\033[1m'
readonly RST='\033[0m'

_ts() { date '+%H:%M:%S'; }
_info()  { echo -e "${DIM}$(_ts)${RST} ${BLU}INFO${RST}  $*"; }
_ok()    { echo -e "${DIM}$(_ts)${RST} ${GRN} OK ${RST}  $*"; }
_warn()  { echo -e "${DIM}$(_ts)${RST} ${YLW}WARN${RST}  $*"; }
_err()   { echo -e "${DIM}$(_ts)${RST} ${RED}FAIL${RST}  $*" >&2; }
_fatal() { _err "$@"; exit 1; }

# ── Argument parsing ────────────────────────────────────────────────────
MODE="interactive"     # interactive | apply | dry-run
ENV_FILE=""            # auto-detect if empty
RETIRE_OLD="false"     # keep previous client keys valid unless asked not to

while [[ $# -gt 0 ]]; do
    case "$1" in
        --apply)      MODE="apply";   shift ;;
        --dry-run)    MODE="dry-run"; shift ;;
        --env)        ENV_FILE="$2";  shift 2 ;;
        --retire-old) RETIRE_OLD="true"; shift ;;
        -h|--help)
            sed -n '2,/^$/s/^# \?//p' "$0"
            exit 0
            ;;
        *) _fatal "Unknown flag: $1 (try --help)" ;;
    esac
done

# ── Resolve .env path ───────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

if [[ -z "$ENV_FILE" ]]; then
    ENV_FILE="$PROJECT_ROOT/.env"
fi

if [[ ! -f "$ENV_FILE" ]]; then
    _fatal ".env not found at $ENV_FILE"
fi

_info "Target: ${BLD}$ENV_FILE${RST}"
_info "Mode:   ${BLD}$MODE${RST}"
echo ""

# ── Validate entropy source ─────────────────────────────────────────────
if ! command -v openssl &>/dev/null; then
    _fatal "openssl not found in PATH — cannot generate cryptographic randomness"
fi

# Smoke-test: generate 1 byte to ensure the CSPRNG is available
if ! openssl rand -hex 1 &>/dev/null; then
    _fatal "openssl rand failed — check system entropy (rng-tools, haveged)"
fi

_ok "Entropy source: openssl CSPRNG"

# ── Generate new secrets ────────────────────────────────────────────────
_info "Generating new secrets..."

NEW_MASTER_KEY=$(openssl rand -hex 32)
NEW_IDENTITY_SECRET=$(openssl rand -hex 32)
NEW_FEDERATION_SECRET=$(openssl rand -hex 32)
NEW_API_KEY=$(openssl rand -hex 16)

# Validate lengths (paranoid — openssl should always produce correct output)
_validate_hex() {
    local name="$1" value="$2" expected_len="$3"
    local actual_len=${#value}
    if [[ $actual_len -ne $expected_len ]]; then
        _fatal "$name: expected $expected_len hex chars, got $actual_len"
    fi
    if ! [[ "$value" =~ ^[0-9a-f]+$ ]]; then
        _fatal "$name: contains non-hex characters"
    fi
}

_validate_hex "MASTER_KEY"        "$NEW_MASTER_KEY"        64
_validate_hex "IDENTITY_SECRET"   "$NEW_IDENTITY_SECRET"   64
_validate_hex "FEDERATION_SECRET" "$NEW_FEDERATION_SECRET" 64
_validate_hex "API_KEY"           "$NEW_API_KEY"            32

_ok "All secrets generated and validated"
echo ""

# ── Display preview (masked) ────────────────────────────────────────────
_mask() {
    local v="$1"
    local len=${#v}
    if [[ $len -le 12 ]]; then
        echo "${v:0:4}…"
    else
        echo "${v:0:6}…${v: -4}"
    fi
}

echo -e "${BLD}┌─────────────────────────────────────────────────────────┐${RST}"
echo -e "${BLD}│  Secret Rotation Preview                                │${RST}"
echo -e "${BLD}├─────────────────────────────────────────────────────────┤${RST}"
printf  "${BLD}│${RST}  %-28s ${CYN}%s${RST}\n" "LLM_PROXY_MASTER_KEY"      "$(_mask "$NEW_MASTER_KEY")      │"
printf  "${BLD}│${RST}  %-28s ${CYN}%s${RST}\n" "LLM_PROXY_IDENTITY_SECRET" "$(_mask "$NEW_IDENTITY_SECRET") │"
printf  "${BLD}│${RST}  %-28s ${CYN}%s${RST}\n" "LLM_PROXY_FEDERATION_SECRET" "$(_mask "$NEW_FEDERATION_SECRET") │"
printf  "${BLD}│${RST}  %-28s ${CYN}%s${RST}\n" "LLM_PROXY_API_KEYS"        "$(_mask "$NEW_API_KEY")          │"
echo -e "${BLD}│${RST}  ${DIM}(256-bit / 256-bit / 256-bit / 128-bit)${RST}                │"
echo -e "${BLD}└─────────────────────────────────────────────────────────┘${RST}"
echo ""

# ── Dry-run exit ─────────────────────────────────────────────────────────
if [[ "$MODE" == "dry-run" ]]; then
    _info "Dry-run complete. No files modified."
    exit 0
fi

# ── Interactive confirmation ─────────────────────────────────────────────
if [[ "$MODE" == "interactive" ]]; then
    echo -e "${YLW}⚠  This will:${RST}"
    echo "   1. Back up current .env"
    echo "   2. Replace 4 secret values in-place"
    echo "   3. Require a proxy restart to take effect"
    echo ""
    read -rp "$(echo -e "${BLD}Proceed? [y/N] ${RST}")" confirm
    if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
        _info "Aborted."
        exit 0
    fi
    echo ""
fi

# ── Backup ───────────────────────────────────────────────────────────────
EPOCH=$(date +%s)
BACKUP="$ENV_FILE.bak.$EPOCH"
cp "$ENV_FILE" "$BACKUP"
chmod 600 "$BACKUP"
_ok "Backup: $BACKUP"

# ── Atomic .env rewrite ──────────────────────────────────────────────────
# Strategy: read current .env, update/append the 4 keys, write to tmp,
# mv over original. mv is atomic on the same filesystem (POSIX guarantee).

TMP_ENV=$(mktemp "$ENV_FILE.tmp.XXXXXX")
trap 'rm -f "$TMP_ENV"' EXIT

_update_or_append() {
    local key="$1" value="$2" file="$3"
    # Match: KEY=... or # KEY=... (commented out in template .env)
    if grep -qE "^#?[[:space:]]*${key}=" "$file" 2>/dev/null; then
        # Replace the first matching line, DROP all subsequent duplicates.
        # This handles .env files with both `# KEY=` (template) and `KEY=val` (active).
        awk -v k="$key" -v v="$value" '
            index($0, k"=") && ($0 ~ /^#?[[:space:]]*/) {
                if (!done) {
                    print k"="v
                    done = 1
                }
                next
            }
            { print }
        ' "$file" > "$file.awktmp" && mv "$file.awktmp" "$file"
    else
        # Key not present — append
        echo "${key}=${value}" >> "$file"
    fi
}

# Put the new key at the FRONT of the existing comma-separated bag, keeping
# the old ones valid. --retire-old drops everything but the newest.
_prepend_api_key() {
    local value="$1" file="$2"
    local existing=""
    if grep -qE "^LLM_PROXY_API_KEYS=" "$file"; then
        existing=$(grep -E "^LLM_PROXY_API_KEYS=" "$file" | head -1 | cut -d= -f2-)
    fi
    if [[ "$RETIRE_OLD" == "true" || -z "$existing" ]]; then
        _update_or_append "LLM_PROXY_API_KEYS" "$value" "$file"
    else
        _update_or_append "LLM_PROXY_API_KEYS" "${value},${existing}" "$file"
    fi
}

cp "$ENV_FILE" "$TMP_ENV"

_update_or_append "LLM_PROXY_MASTER_KEY"        "$NEW_MASTER_KEY"        "$TMP_ENV"
_update_or_append "LLM_PROXY_IDENTITY_SECRET"   "$NEW_IDENTITY_SECRET"   "$TMP_ENV"
_update_or_append "LLM_PROXY_FEDERATION_SECRET" "$NEW_FEDERATION_SECRET" "$TMP_ENV"
# APPEND, do not replace. The bag is a comma-separated list precisely so a
# rotation can have an overlap window, and this used to overwrite it with a
# single key — so every existing client died at the restart and the operator
# had a hard cutover with no staging. The new key goes first; retire the old
# one with --retire-old once consumers have moved.
_prepend_api_key "$NEW_API_KEY" "$TMP_ENV"

# Verify the tmp file has all 4 keys with non-empty values
_verify_key() {
    local key="$1" file="$2"
    local val
    val=$(grep -E "^${key}=" "$file" | head -1 | cut -d= -f2-)
    if [[ -z "$val" ]]; then
        _fatal "Verification failed: $key is empty in staged file"
    fi
}

_verify_key "LLM_PROXY_MASTER_KEY"        "$TMP_ENV"
_verify_key "LLM_PROXY_IDENTITY_SECRET"   "$TMP_ENV"
_verify_key "LLM_PROXY_FEDERATION_SECRET" "$TMP_ENV"
_verify_key "LLM_PROXY_API_KEYS"          "$TMP_ENV"

# Permissions: .env should be owner-only
chmod 600 "$TMP_ENV"

# Atomic swap
mv "$TMP_ENV" "$ENV_FILE"
trap - EXIT  # disarm cleanup — file is already moved

_ok "Secrets written to $ENV_FILE"
echo ""

# ── Salt file warning ────────────────────────────────────────────────────
# Resolve the salt the way core/secrets.py does. This used to hardcode
# "$PROJECT_ROOT/.llmproxy_salt" — the location the salt moved AWAY from in
# 1.34.0 — so on any install created since, the file did not exist and the
# entire warning block below was skipped silently. An operator rotating the
# master key got a success banner and no mention of the salt at all.
if [[ -n "${LLM_PROXY_SALT_PATH:-}" ]]; then
    SALT_FILE="$LLM_PROXY_SALT_PATH"
elif [[ -f "$PROJECT_ROOT/data/.llmproxy_salt" ]]; then
    SALT_FILE="$PROJECT_ROOT/data/.llmproxy_salt"
else
    SALT_FILE="$PROJECT_ROOT/.llmproxy_salt"   # legacy location, still honoured
fi
if [[ -f "$SALT_FILE" ]]; then
    echo -e "${YLW}┌─────────────────────────────────────────────────────────┐${RST}"
    echo -e "${YLW}│  ⚠  MASTER_KEY changed — PBKDF2 salt action needed     │${RST}"
    echo -e "${YLW}├─────────────────────────────────────────────────────────┤${RST}"
    echo -e "${YLW}│${RST}  The old MASTER_KEY derived a Fernet key using:         │"
    echo -e "${YLW}│${RST}    ${DIM}$SALT_FILE${RST}"
    echo -e "${YLW}│${RST}                                                         │"
    echo -e "${YLW}│${RST}  Any values encrypted under the old key are now         │"
    echo -e "${YLW}│${RST}  undecryptable.  Note: nothing in the proxy currently   │"
    echo -e "${YLW}│${RST}  calls SecretManager.encrypt, so on a stock install     │"
    echo -e "${YLW}│${RST}  there is nothing to migrate.  Choose one:              │"
    echo -e "${YLW}│${RST}                                                         │"
    echo -e "${YLW}│${RST}  ${BLD}Option A${RST} — Clean break (recommended):                 │"
    echo -e "${YLW}│${RST}    rm $SALT_FILE"
    echo -e "${YLW}│${RST}    Proxy will regenerate salt on next boot.              │"
    echo -e "${YLW}│${RST}    Re-enter provider API keys via the admin UI.          │"
    echo -e "${YLW}│${RST}                                                         │"
    echo -e "${YLW}│${RST}  ${BLD}Option B${RST} — Migrate (advanced):                         │"
    echo -e "${YLW}│${RST}    Decrypt all values with the old key, re-encrypt       │"
    echo -e "${YLW}│${RST}    with the new key. See core/secrets.py for the KDF.    │"
    echo -e "${YLW}│${RST}                                                         │"
    echo -e "${YLW}└─────────────────────────────────────────────────────────┘${RST}"
    echo ""
fi

# ── Post-rotation checklist ──────────────────────────────────────────────
echo -e "${GRN}┌─────────────────────────────────────────────────────────┐${RST}"
echo -e "${GRN}│  ✅  Rotation complete — post-rotation checklist        │${RST}"
echo -e "${GRN}├─────────────────────────────────────────────────────────┤${RST}"
echo -e "${GRN}│${RST}                                                         │"
echo -e "${GRN}│${RST}  ${BLD}1.${RST} Restart the proxy:                                  │"
echo -e "${GRN}│${RST}     ${DIM}make restart${RST}  or  ${DIM}docker compose restart proxy${RST}      │"
echo -e "${GRN}│${RST}                                                         │"
echo -e "${GRN}│${RST}  ${BLD}2.${RST} Distribute new API key to SDK consumers:            │"
echo -e "${GRN}│${RST}     ${CYN}$(_mask "$NEW_API_KEY")${RST}                                         │"
echo -e "${GRN}│${RST}     ${DIM}(full value in .env — never log or share it)${RST}        │"
echo -e "${GRN}│${RST}                                                         │"
echo -e "${GRN}│${RST}  ${BLD}3.${RST} Verify health:                                      │"
echo -e "${GRN}│${RST}     ${DIM}curl -sS http://localhost:8090/health | jq .${RST}        │"
echo -e "${GRN}│${RST}                                                         │"
echo -e "${GRN}│${RST}  ${BLD}4.${RST} Rotate 3rd-party keys separately:                   │"
echo -e "${GRN}│${RST}     ${DIM}• Groq:   https://console.groq.com/keys${RST}             │"
echo -e "${GRN}│${RST}     ${DIM}• Google: https://aistudio.google.com/app/apikey${RST}    │"
echo -e "${GRN}│${RST}     ${DIM}• OpenAI: https://platform.openai.com/api-keys${RST}     │"
echo -e "${GRN}│${RST}                                                         │"
echo -e "${GRN}│${RST}  ${BLD}5.${RST} Delete the backup when satisfied:                   │"
echo -e "${GRN}│${RST}     ${DIM}rm $BACKUP${RST}"
echo -e "${GRN}│${RST}                                                         │"
echo -e "${GRN}└─────────────────────────────────────────────────────────┘${RST}"

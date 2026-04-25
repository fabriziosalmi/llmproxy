#!/usr/bin/env bash
# ==============================================================================
#  LLMProxy — Guided installer
#
#  Detects the platform, verifies prerequisites, and launches the proxy either
#  via Docker Compose v2 or a local Python 3.12+ virtualenv.
#
#  Usage:
#    ./install.sh                    # interactive
#    ./install.sh --docker           # non-interactive Docker install
#    ./install.sh --local            # non-interactive venv install
#    ./install.sh --check            # only verify prerequisites, no install
#    ./install.sh --yes              # accept defaults (Docker if available, else venv)
# ==============================================================================
set -u
# Intentionally NOT using `set -e`: we want to report every missing prereq
# instead of aborting on the first one.

C_RESET="\033[0m"; C_BOLD="\033[1m"
C_RED="\033[38;5;196m"; C_GREEN="\033[38;5;46m"; C_YELLOW="\033[38;5;226m"
C_BLUE="\033[38;5;39m"; C_CYAN="\033[38;5;51m"; C_GRAY="\033[38;5;242m"

say()  { printf '%b\n' "$*"; }
info() { printf "${C_BLUE}[ i ]${C_RESET} %s\n" "$*"; }
ok()   { printf "${C_GREEN}[ \xe2\x9c\x93 ]${C_RESET} %s\n" "$*"; }
warn() { printf "${C_YELLOW}[ ! ]${C_RESET} %s\n" "$*"; }
err()  { printf "${C_RED}[ x ]${C_RESET} %s\n" "$*"; }
hint() { printf "${C_GRAY}      %s${C_RESET}\n" "$*"; }

print_banner() {
    printf "${C_CYAN}%s${C_RESET}\n" \
"  _    _     __  __ ___                    "
    printf "${C_CYAN}%s${C_RESET}\n" \
" | |  | |   |  \/  | _ \\ _ _ _____ ___  _   "
    printf "${C_CYAN}%s${C_RESET}\n" \
" | |__| |__ | |\/| |  _/ '_/ _ \ \/ | || |  "
    printf "${C_CYAN}%s${C_RESET}\n" \
" |____|___||_|  |_|_| |_| \\___/_\\_\\\\_, |  "
    printf "${C_CYAN}%s${C_RESET}\n" \
"                                    |__/    "
    printf "${C_GRAY}  Security gateway for LLMs — guided installer${C_RESET}\n\n"
}

# ── Platform detection ────────────────────────────────────────────────────────

OS="$(uname -s)"
DISTRO=""
if [[ "$OS" == "Linux" && -r /etc/os-release ]]; then
    # shellcheck disable=SC1091
    DISTRO="$(. /etc/os-release && echo "${ID:-unknown}")"
fi

# ── Prerequisite checks ───────────────────────────────────────────────────────

HAVE_PY312=0
PY312_BIN=""
HAVE_DOCKER=0
HAVE_COMPOSE_V2=0
HAVE_COMPOSE_V1_ONLY=0
HAVE_CURL=0

detect_python312() {
    for bin in python3.12 python3.13 python3; do
        if command -v "$bin" >/dev/null 2>&1; then
            local v
            v="$($bin -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || true)"
            [[ -z "$v" ]] && continue
            local major="${v%.*}"; local minor="${v#*.}"
            if [[ "$major" -ge 3 && "$minor" -ge 12 ]]; then
                HAVE_PY312=1
                PY312_BIN="$bin"
                return
            fi
        fi
    done
}

detect_docker() {
    if command -v docker >/dev/null 2>&1; then
        if docker info >/dev/null 2>&1; then
            HAVE_DOCKER=1
        fi
    fi
    if [[ "$HAVE_DOCKER" -eq 1 ]]; then
        if docker compose version >/dev/null 2>&1; then
            HAVE_COMPOSE_V2=1
        elif command -v docker-compose >/dev/null 2>&1; then
            HAVE_COMPOSE_V1_ONLY=1
        fi
    fi
}

detect_curl() { command -v curl >/dev/null 2>&1 && HAVE_CURL=1; }

print_python_install_hint() {
    case "$DISTRO" in
        ubuntu|debian)
            hint "Ubuntu/Debian: sudo add-apt-repository ppa:deadsnakes/ppa && \\"
            hint "               sudo apt update && sudo apt install -y python3.12 python3.12-venv"
            ;;
        fedora|rhel|centos|rocky|almalinux)
            hint "Fedora/RHEL: sudo dnf install -y python3.12"
            ;;
        alpine)
            hint "Alpine: apk add python3 py3-pip  (ensure version >= 3.12)"
            ;;
        arch|manjaro)
            hint "Arch: sudo pacman -S python"
            ;;
        *)
            if [[ "$OS" == "Darwin" ]]; then
                hint "macOS: brew install python@3.12"
            else
                hint "Install Python 3.12+ from https://www.python.org/downloads/"
            fi
            ;;
    esac
}

print_docker_install_hint() {
    case "$DISTRO" in
        ubuntu|debian)
            hint "Ubuntu/Debian: https://docs.docker.com/engine/install/ubuntu/"
            hint "              (installs Docker Engine + the 'docker compose' v2 plugin)"
            ;;
        fedora|rhel|centos|rocky|almalinux)
            hint "RHEL family: https://docs.docker.com/engine/install/rhel/"
            ;;
        *)
            if [[ "$OS" == "Darwin" ]]; then
                hint "macOS: brew install --cask docker  (Docker Desktop)"
            else
                hint "See https://docs.docker.com/engine/install/"
            fi
            ;;
    esac
}

run_prereq_checks() {
    info "Detecting platform..."
    ok "OS: $OS${DISTRO:+ ($DISTRO)}"

    detect_python312
    detect_docker
    detect_curl

    echo
    info "Prerequisites:"

    if [[ "$HAVE_PY312" -eq 1 ]]; then
        ok "Python 3.12+ found: $PY312_BIN ($($PY312_BIN --version 2>&1))"
    else
        warn "Python 3.12+ not found"
        print_python_install_hint
    fi

    if [[ "$HAVE_DOCKER" -eq 1 ]]; then
        ok "Docker daemon reachable ($(docker --version 2>/dev/null | head -1))"
        if [[ "$HAVE_COMPOSE_V2" -eq 1 ]]; then
            ok "Docker Compose v2 plugin present ($(docker compose version --short 2>/dev/null))"
        elif [[ "$HAVE_COMPOSE_V1_ONLY" -eq 1 ]]; then
            err "Only legacy 'docker-compose' v1 found — NOT SUPPORTED"
            hint "v1 is abandoned and breaks with modern urllib3 (chunked kwarg error)."
            hint "Install the v2 plugin:"
            case "$DISTRO" in
                ubuntu|debian)
                    hint "  sudo apt install -y docker-compose-plugin"
                    ;;
                *)
                    hint "  https://docs.docker.com/compose/install/linux/"
                    ;;
            esac
        else
            warn "No Docker Compose found"
            print_docker_install_hint
        fi
    else
        warn "Docker not available (daemon unreachable or not installed)"
        print_docker_install_hint
    fi

    if [[ "$HAVE_CURL" -eq 1 ]]; then
        ok "curl present"
    else
        warn "curl not found — health check will be skipped"
    fi
    echo
}

# ── Env bootstrap ─────────────────────────────────────────────────────────────

gen_proxy_key() {
    # Prefer openssl (universally available), fall back to Python, then to
    # /dev/urandom via hexdump as a last resort. No dependency on a specific
    # Python version — the Docker install path must work without system Python.
    if command -v openssl >/dev/null 2>&1; then
        printf 'sk-proxy-%s' "$(openssl rand -hex 16)"
    elif command -v python3 >/dev/null 2>&1; then
        python3 -c 'import secrets; print(f"sk-proxy-{secrets.token_hex(16)}")'
    elif [[ -r /dev/urandom ]] && command -v hexdump >/dev/null 2>&1; then
        printf 'sk-proxy-%s' "$(hexdump -n16 -e '16/1 "%02x"' /dev/urandom)"
    else
        printf 'sk-proxy-%s' "$(date +%s%N | cksum | awk '{print $1}')"
    fi
}

bootstrap_env() {
    if [[ -f ".env" ]]; then
        ok ".env already present — leaving untouched"
        return
    fi
    if [[ ! -f ".env.example" ]]; then
        err ".env.example missing — cannot bootstrap"
        return 1
    fi
    cp .env.example .env
    local key
    key="$(gen_proxy_key)"
    # Replace the placeholder value using awk — works identically on BSD and
    # GNU userlands without worrying about sed's -i portability quirks.
    awk -v key="$key" '
        /^LLM_PROXY_API_KEYS=sk-proxy-CHANGE-ME$/ { print "LLM_PROXY_API_KEYS=" key; next }
        { print }
    ' .env > .env.tmp && mv .env.tmp .env
    ok ".env created. Auto-generated proxy auth key:"
    printf "      ${C_BOLD}%s${C_RESET}\n" "$key"
    echo
    warn "No provider API keys set yet. The proxy will start in ONBOARDING MODE."
    hint "Add cloud provider keys in .env (OPENAI_API_KEY, ANTHROPIC_API_KEY, ...)"
    hint "Or configure a local OpenAI-compatible endpoint (LM Studio, vLLM, Ollama):"
    hint "  LLM_PROXY_ENDPOINT_LOCAL_URL=http://192.168.1.10:1234/v1"
    hint "  LLM_PROXY_ENDPOINT_LOCAL_MODELS=llama-3.3-70b,qwen-2.5-coder"
    hint "Or open the admin UI after startup and use the onboarding wizard."
    echo
}

# ── Install paths ─────────────────────────────────────────────────────────────

install_docker() {
    info "Starting via Docker Compose v2..."
    docker compose up -d --build
    local rc=$?
    if [[ $rc -ne 0 ]]; then
        err "docker compose failed (exit $rc). Check the output above."
        return $rc
    fi
    ok "Container started."
    post_start_healthcheck
}

build_ui_if_possible() {
    # Build the optimized UI bundle when Node is present. Without a build,
    # the proxy still works but Tailwind utility classes are unstyled
    # (CSP no longer allows the JIT CDN — see CHANGELOG 1.12.1).
    if ! command -v npm >/dev/null 2>&1; then
        warn "npm not found — skipping UI build."
        warn "The admin console will load but Tailwind classes will not be styled."
        hint "Install Node 20+ for the optimized bundle: https://nodejs.org/"
        return 0
    fi
    info "Building UI bundle (Vite)..."
    (cd ui && npm install --no-audit --no-fund >/dev/null 2>&1 && npm run build >/dev/null 2>&1)
    local rc=$?
    if [[ $rc -ne 0 ]]; then
        warn "UI build failed (rc=$rc) — admin console will load unstyled."
        hint "Run 'cd ui && npm install && npm run build' manually to see the error."
    else
        ok "UI bundle ready at ui/dist/"
    fi
}

install_local() {
    info "Starting via local Python venv..."
    if [[ ! -d venv ]]; then
        "$PY312_BIN" -m venv venv
    fi
    # shellcheck disable=SC1091
    . venv/bin/activate
    python -m pip install --upgrade pip wheel >/dev/null
    info "Installing dependencies (this takes a few minutes on first run)..."
    python -m pip install -r requirements.txt
    local rc=$?
    if [[ $rc -ne 0 ]]; then
        err "pip install failed."
        return $rc
    fi
    ok "Dependencies installed."
    build_ui_if_possible
    info "Launching the proxy (background, logs -> .proxy.log)..."
    nohup python main.py >.proxy.log 2>&1 &
    echo $! > .proxy.pid
    sleep 3
    post_start_healthcheck
}

post_start_healthcheck() {
    if [[ "$HAVE_CURL" -ne 1 ]]; then
        warn "curl missing — skipping health check"
        show_next_steps
        return
    fi
    info "Probing http://localhost:8090/health ..."
    local attempts=0
    while (( attempts < 15 )); do
        if curl -fsS http://localhost:8090/health >/dev/null 2>&1; then
            ok "Proxy is UP"
            show_next_steps
            return
        fi
        sleep 1
        attempts=$((attempts + 1))
    done
    warn "Proxy did not respond within 15s — check logs:"
    if [[ -f .proxy.log ]]; then hint "tail -f .proxy.log"; fi
    hint "docker compose logs -f llmproxy   (if Docker)"
    show_next_steps
}

show_next_steps() {
    echo
    say "${C_BOLD}Next steps${C_RESET}"
    say "  Admin UI:    ${C_CYAN}http://localhost:8090/ui${C_RESET}"
    say "  Health:      curl http://localhost:8090/health"
    say "  API smoke:   curl -H 'Authorization: Bearer \$YOUR_KEY' http://localhost:8090/v1/models"
    say "  Stop (Docker): docker compose down"
    say "  Stop (local):  ./proxy.sh stop"
    echo
}

# ── CLI ───────────────────────────────────────────────────────────────────────

MODE=""
ACCEPT_DEFAULTS=0
CHECK_ONLY=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --docker)  MODE="docker"; shift ;;
        --local)   MODE="local";  shift ;;
        --check)   CHECK_ONLY=1;  shift ;;
        --yes|-y)  ACCEPT_DEFAULTS=1; shift ;;
        -h|--help)
            sed -n '2,20p' "$0"
            exit 0
            ;;
        *)
            err "Unknown option: $1"
            exit 2
            ;;
    esac
done

print_banner
run_prereq_checks

if [[ "$CHECK_ONLY" -eq 1 ]]; then
    if [[ "$HAVE_PY312" -eq 0 && "$HAVE_COMPOSE_V2" -eq 0 ]]; then
        err "No viable install path (need Python 3.12+ OR Docker + compose v2)."
        exit 1
    fi
    ok "Prerequisites sufficient for install."
    exit 0
fi

if [[ "$HAVE_COMPOSE_V1_ONLY" -eq 1 && "$HAVE_COMPOSE_V2" -eq 0 ]]; then
    err "Installation aborted: legacy docker-compose v1 is installed but v2 is required."
    err "Fix it first, then re-run ./install.sh — see instructions above."
    exit 1
fi

if [[ -z "$MODE" ]]; then
    if [[ "$ACCEPT_DEFAULTS" -eq 1 ]]; then
        if [[ "$HAVE_COMPOSE_V2" -eq 1 ]]; then MODE="docker"; else MODE="local"; fi
    else
        say "${C_BOLD}Choose installation mode:${C_RESET}"
        [[ "$HAVE_COMPOSE_V2" -eq 1 ]] && say "  ${C_BOLD}[D]${C_RESET} Docker Compose  (recommended)"
        [[ "$HAVE_PY312"      -eq 1 ]] && say "  ${C_BOLD}[L]${C_RESET} Local venv (Python $($PY312_BIN --version 2>&1 | awk '{print $2}'))"
        say "  ${C_BOLD}[Q]${C_RESET} Quit"
        printf "Selection [%s]: " "$([[ "$HAVE_COMPOSE_V2" -eq 1 ]] && echo D || echo L)"
        read -r choice
        case "${choice:-}" in
            ""|d|D) MODE="docker" ;;
            l|L)    MODE="local"  ;;
            q|Q)    info "Aborted."; exit 0 ;;
            *)      err "Unknown choice."; exit 2 ;;
        esac
    fi
fi

case "$MODE" in
    docker)
        if [[ "$HAVE_COMPOSE_V2" -ne 1 ]]; then
            err "Docker Compose v2 not available."
            exit 1
        fi
        bootstrap_env
        install_docker
        ;;
    local)
        if [[ "$HAVE_PY312" -ne 1 ]]; then
            err "Python 3.12+ not available — cannot use local install."
            exit 1
        fi
        bootstrap_env
        install_local
        ;;
esac

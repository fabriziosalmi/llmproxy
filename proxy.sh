#!/usr/bin/env bash

# ==============================================================================
# LLMPROXY Command Line Interface
# FAANG-Level 10x Daemon Manager
# ==============================================================================

# Modern Terminal Colors
C_RESET="\033[0m"
C_GREEN="\033[38;5;46m"
C_CYAN="\033[38;5;51m"
C_BLUE="\033[38;5;33m"
C_RED="\033[38;5;196m"
C_ORANGE="\033[38;5;208m"
C_GRAY="\033[38;5;242m"
C_WHITE="\033[1;37m"
C_BOLD="\033[1m"

PID_FILE=".proxy.pid"
LOG_FILE=".proxy.log"

# Default configuration parameters
PORT=8090
DEBUG=false

print_banner() {
    echo -e "${C_CYAN}"
    echo "    __    __    __  ______  ____  ____  ________  ____  __ "
    echo "   / /   / /   /  |/  / _ \/ __ \/ __ \/ __/ __ \/ /\ \/ / "
    echo "  / /   / /   / /|_/ / /_/ / /_/ / /_/ / /_/ / / /    \  /  "
    echo " / /___/ /___/ /  / / ____/ _, _/ _, _/ __/ /_/ /     / /   "
    echo "/_____/_____/_/  /_/_/   /_/ |_/_/ |_/_/  \____/     /_/    "
    echo -e "       v0.10.x // Neural Firewall Edition${C_RESET}"
    echo ""
}

print_info()    { echo -e "${C_BLUE}[ i ]${C_RESET} $1"; }
print_success() { echo -e "${C_GREEN}[ + ]${C_RESET} $1"; }
print_error()   { echo -e "${C_RED}[ - ]${C_RESET} $1"; }
print_warn()    { echo -e "${C_ORANGE}[ ! ]${C_RESET} $1"; }
print_debug()   { if [ "$DEBUG" = true ]; then echo -e "${C_GRAY}[ d ] $1${C_RESET}"; fi }

show_help() {
    print_banner
    echo -e "${C_BOLD}USAGE:${C_RESET} ./$(basename $0) [COMMAND] [OPTIONS]"
    echo ""
    echo -e "${C_BOLD}COMMANDS:${C_RESET}"
    echo "  start       Boot the proxy backend and required containers (Redis)"
    echo "  stop        Gracefully halt the proxy and exterminate zombie threads"
    echo "  restart     Restart the proxy subsystem"
    echo "  status      Check the daemon running state and bound ports"
    echo "  logs        Tail the proxy telemetry logs"
    echo "  reset       Wipe the runtime logs, PIDs, and local cache"
    echo ""
    echo -e "${C_BOLD}OPTIONS:${C_RESET}"
    echo "  -p, --port  Specify the primary binding port (default: 8090)"
    echo "  -d, --debug Enable verbose script and application logs"
    echo "  -h, --help  Display this help interface"
    echo ""
}

check_running() {
    if [ -f "$PID_FILE" ]; then
        orig_pid=$(cat "$PID_FILE")
        if ps -p "$orig_pid" > /dev/null 2>&1; then
            return 0 # Running
        else
            print_debug "Stale PID file detected. Cleaning up..."
            rm "$PID_FILE"
            return 1 # Stale PID
        fi
    fi
    return 1 # Not running
}

start_proxy() {
    if check_running; then
        print_warn "LLMProxy is already running (PID: $(cat $PID_FILE))."
        exit 1
    fi

    # Extra check if ports 8090 or 8081 are silently bound
    zombie=$(lsof -Pi :$PORT -sTCP:LISTEN -t 2>/dev/null || lsof -Pi :8081 -sTCP:LISTEN -t 2>/dev/null)
    if [ ! -z "$zombie" ]; then
        print_error "Port $PORT or 8081 is already bound by another process (PID(s): $(echo $zombie | tr '\n' ' '))."
        print_info "Run './proxy.sh stop' to exterminate it first."
        exit 1
    fi

    print_info "Booting Neural Firewall & Proxy subsystems on port $PORT..."
    
    # Start Dockerized Redis Server for Trajectory Buffers
    if ! docker ps -q -f name=llmproxy-redis > /dev/null; then
        if docker ps -aq -f status=exited -f name=llmproxy-redis > /dev/null; then
            print_info "Starting existing llmproxy-redis Docker container..."
            docker start llmproxy-redis > /dev/null
        else
            print_info "Spawning new llmproxy-redis Docker container (redis:alpine)..."
            docker run -d --name llmproxy-redis -p 6379:6379 redis:alpine > /dev/null
        fi
        print_debug "Redis container command successfully dispatched."
    else
        print_success "Dockerized redis (llmproxy-redis) is already running."
    fi

    if [ ! -d "venv" ]; then
        print_error "Virtual environment 'venv' not found in current directory."
        exit 1
    fi
    
    source venv/bin/activate
    export PROXY_PORT=$PORT
    
    if [ "$DEBUG" = true ]; then
        export LOG_LEVEL="DEBUG"
        print_debug "DEBUG mode engaged. Tailing logs massively."
    fi
    
    # Run the raw server daemonized
    nohup python main.py > "$LOG_FILE" 2>&1 &
    new_pid=$!
    echo $new_pid > "$PID_FILE"
    
    print_debug "Background python process dispatched with PID $new_pid"
    sleep 3
    if check_running; then
        print_success "LLMProxy daemonized successfully! (PID: $new_pid)"
        print_info "Log output shifted to: $LOG_FILE"
        print_info "Admin HUD available at: http://127.0.0.1:$PORT/ui"
    else
        print_error "Failed to start LLMProxy. Check $LOG_FILE for details."
        exit 1
    fi
}

stop_proxy() {
    if check_running; then
        orig_pid=$(cat "$PID_FILE")
        print_info "Sending SIGTERM to proxy (PID: $orig_pid)..."
        kill "$orig_pid"
        
        # Wait for graceful shutdown
        for i in {1..7}; do
            if ! check_running; then break; fi
            sleep 1
        done
        
        if check_running; then
            print_warn "Graceful shutdown failed. Sending SIGKILL..."
            kill -9 "$orig_pid" > /dev/null 2>&1
        fi
        
        rm -f "$PID_FILE"
        print_success "Proxy subsystems halted."
    else
        # If no PID file but lsof shows a bound port
        zombie=$(lsof -Pi :$PORT -sTCP:LISTEN -t 2>/dev/null || lsof -Pi :8081 -sTCP:LISTEN -t 2>/dev/null)
        if [ ! -z "$zombie" ]; then
            print_warn "Zombie proxy found on port $PORT/8081 (PID: $(echo $zombie | tr '\n' ' ')). Exterminating..."
            kill -9 $zombie 2>/dev/null
            print_success "Proxy subsystems halted."
        else
            print_warn "LLMProxy is not currently running."
        fi
        rm -f "$PID_FILE"
    fi
}

status_proxy() {
    if check_running; then
        orig_pid=$(cat "$PID_FILE")
        print_success "LLMProxy is ${C_GREEN}ONLINE${C_RESET} (PID: $orig_pid)"
        print_info "Logs: tail -f $LOG_FILE"
    else
        zombie=$(lsof -Pi :$PORT -sTCP:LISTEN -t 2>/dev/null)
        if [ ! -z "$zombie" ]; then
            print_warn "LLMProxy PID lost, but port $PORT is bound (PID: $(echo $zombie | tr '\n' ' '))."
        else
            print_warn "LLMProxy is ${C_RED}OFFLINE${C_RESET}"
        fi
    fi
    
    if docker ps -q -f name=llmproxy-redis > /dev/null; then
        print_success "Redis backend is ${C_GREEN}ONLINE${C_RESET}"
    else
        print_warn "Redis backend is ${C_RED}OFFLINE${C_RESET}"
    fi
}

logs_proxy() {
    if [ -f "$LOG_FILE" ]; then
        print_info "Following neural logs (Ctrl+C to exit)..."
        tail -f "$LOG_FILE"
    else
        print_error "Log file not found. System has not been booted yet."
    fi
}

reset_proxy() {
    print_warn "Are you sure you want to wipe local PIDs and logs? [y/N]"
    read -r response
    if [[ "$response" =~ ^([yY][eE][sS]|[yY])$ ]]; then
        stop_proxy
        rm -f "$LOG_FILE" "$PID_FILE"
        print_success "Runtime environment cleanly reset."
    else
        print_info "Aborted."
    fi
}

# ----------------- ARGS PARSER -----------------
COMMAND=""

while [[ $# -gt 0 ]]; do
    case $1 in
        -d|--debug)
            DEBUG=true
            shift
            ;;
        -p|--port)
            PORT="$2"
            shift 2
            ;;
        -h|--help)
            show_help
            exit 0
            ;;
        start|stop|restart|status|logs|reset)
            COMMAND="$1"
            shift
            ;;
        *)
            print_error "Unknown option or command: $1"
            show_help
            exit 1
            ;;
    esac
done

if [ -z "$COMMAND" ]; then
    show_help
    exit 1
fi

if [ "$COMMAND" != "logs" ] && [ "$COMMAND" != "status" ] && [ "$COMMAND" != "reset" ]; then
    print_banner
fi

case "$COMMAND" in
    start)   start_proxy ;;
    stop)    stop_proxy ;;
    restart) stop_proxy; sleep 1; start_proxy ;;
    status)  status_proxy ;;
    logs)    logs_proxy ;;
    reset)   reset_proxy ;;
esac

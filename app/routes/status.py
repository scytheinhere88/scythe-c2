"""
Status routes for SCYTHE C2.
- /api/status -> full system status (JSON)
- /api/stream -> Server-Sent Events (SSE) for real-time updates
"""

import json
import asyncio
from typing import List

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse

from app.core.logger import logger
from app.core.models import SystemStatus

router = APIRouter(prefix="/api", tags=["status"])


async def _get_status_data(request: Request) -> dict:
    attack_manager = request.app.state.attack_manager
    proxy_manager = request.app.state.proxy_manager
    history_manager = request.app.state.history_manager
    concurrent_manager = request.app.state.concurrent_manager

    active_attacks = attack_manager.get_active_attacks()
    total_rps = sum(a.rps for a in active_attacks)
    total_requests = sum(a.total_requests for a in active_attacks)

    proxy_stats = await proxy_manager.get_stats()
    proxy_pool = proxy_stats.alive if proxy_stats else 0
    proxy_refreshing = False

    max_concurrent = await concurrent_manager.get_max()
    history_entries = await history_manager.get_history(limit=5)

    return {
        "active_attacks": [a.model_dump() for a in active_attacks],
        "total_rps": total_rps,
        "total_requests": total_requests,
        "proxy_pool": proxy_pool,
        "proxy_refreshing": proxy_refreshing,
        "max_concurrent": max_concurrent,
        "history": [h.model_dump() for h in history_entries]
    }


@router.get("/status")
async def get_status(request: Request):
    try:
        data = await _get_status_data(request)
        return data
    except Exception as e:
        logger.error(f"Error in /api/status: {e}")
        return {
            "active_attacks": [],
            "total_rps": 0,
            "total_requests": 0,
            "proxy_pool": 0,
            "proxy_refreshing": False,
            "max_concurrent": 5,
            "history": []
        }


@router.head("/status")
async def head_status():
    return Response(status_code=200)


@router.get("/stream")
async def stream_status(request: Request):
    async def event_generator():
        while True:
            if await request.is_disconnected():
                logger.debug("SSE client disconnected")
                break
            try:
                data = await _get_status_data(request)
                yield {
                    "event": "update",
                    "data": json.dumps(data)
                }
            except Exception as e:
                logger.error(f"SSE error: {e}")
                yield {
                    "event": "error",
                    "data": json.dumps({"error": str(e)})
                }
            await asyncio.sleep(1)

    return EventSourceResponse(event_generator())

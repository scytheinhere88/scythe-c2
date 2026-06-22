"""
History routes for SCYTHE C2.
- GET /api/history       → Get recent history entries
- HEAD /api/history      → Health check for history endpoint
- DELETE /api/history/clear → Clear ALL history (dangerous)
- DELETE /api/history/old?days=3 → Delete entries older than N days
- GET /api/history/stats → Get history statistics
"""

from fastapi import APIRouter, Request, HTTPException, Query, Response

from app.core.logger import logger
from app.core.models import HistoryEntry

router = APIRouter(prefix="/api", tags=["history"])


@router.get("/history")
async def get_history(request: Request, limit: int = Query(5, ge=1, le=100)):
    """
    Get the most recent attack history entries.
    Default limit is 5, maximum 100.
    """
    history_manager = request.app.state.history_manager
    entries = await history_manager.get_history(limit=limit)
    return {"history": [e.model_dump() for e in entries]}


@router.head("/history")
async def head_history():
    """
    HEAD request for health check (admin panel).
    Returns 200 OK if the history service is operational.
    """
    return Response(status_code=200)


@router.delete("/history/clear")
async def clear_all_history(request: Request):
    """
    ⚠️ DANGEROUS: Delete ALL history entries.
    Use with caution – this action is irreversible.
    """
    history_manager = request.app.state.history_manager
    count = await history_manager.clear_all()
    logger.warning(f"All history cleared by admin: {count} entries removed")
    return {
        "success": True,
        "message": f"Cleared {count} history entries",
        "deleted_count": count
    }


@router.delete("/history/old")
async def delete_old_history(
    request: Request,
    days: int = Query(3, ge=1, le=365)
):
    """
    Delete history entries older than the specified number of days.
    Default is 3 days.
    """
    history_manager = request.app.state.history_manager
    count = await history_manager.delete_old_entries(days=days)
    logger.info(f"Deleted {count} history entries older than {days} days")
    return {
        "success": True,
        "message": f"Deleted {count} entries older than {days} days",
        "deleted_count": count
    }


@router.get("/history/stats")
async def get_history_stats(request: Request):
    """
    Get statistics about the history database.
    Returns total entries, total requests, unique domains, average RPS.
    """
    history_manager = request.app.state.history_manager
    stats = await history_manager.get_stats()
    return stats
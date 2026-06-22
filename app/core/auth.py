# app/core/auth.py - FIXED VERSION
"""
Authentication utilities for SCYTHE C2.
Uses simple session-based authentication with password from .env.
"""

import os
import secrets
import logging
from typing import Optional
from fastapi import Request, HTTPException, Depends
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from app.core.config import settings

# ========== LOGGER ==========
logger = logging.getLogger("scythe_c2.auth")

# ========== CONFIGURATION ==========
LOGIN_PASSWORD = os.getenv("LOGIN_PASSWORD", "scythe88")

# Session storage (in-memory)
_sessions = {}  # session_id -> user_data

# ========== SESSION MANAGEMENT ==========

def create_session(username: str = "admin") -> str:
    token = secrets.token_urlsafe(32)
    _sessions[token] = {
        "username": username,
        "created_at": __import__("time").time()
    }
    return token

def get_session(token: str) -> Optional[dict]:
    return _sessions.get(token)

def delete_session(token: str) -> bool:
    if token in _sessions:
        del _sessions[token]
        return True
    return False

def is_authenticated(token: str) -> bool:
    session = get_session(token)
    if session:
        return True
    return False

# ========== DEPENDENCIES FOR FASTAPI ==========

async def require_auth(request: Request):
    """
    Dependency to protect routes.
    Checks for session cookie named 'scythe_session'.
    If not authenticated, raise HTTPException 401 (for API) or redirect (for HTML).
    """
    token = request.cookies.get("scythe_session")
    logger.debug(f"Session token: {token[:10] if token else 'None']}")

    if not token or not is_authenticated(token):
        logger.info("Unauthenticated request")
        
        # Check if request is API (JSON) or HTML page
        accept_header = request.headers.get("accept", "")
        if "application/json" in accept_header:
            # API request → return 401
            raise HTTPException(status_code=401, detail="Unauthorized. Please login.")
        else:
            # HTML page request → redirect to login
            raise HTTPException(status_code=307, headers={"Location": "/login"})
    
    return True

async def optional_auth(request: Request):
    token = request.cookies.get("scythe_session")
    if token and is_authenticated(token):
        return get_session(token)
    return None

# ========== API KEY AUTH ==========

security = HTTPBearer()

async def require_api_key(credentials: HTTPAuthorizationCredentials = Depends(security)):
    api_key = os.getenv("API_KEY", "scythe-api-key-2025")
    if credentials.credentials != api_key:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return credentials.credentials
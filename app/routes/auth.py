from fastapi import APIRouter, Request, Response, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
import logging

from app.core.auth import create_session, delete_session, LOGIN_PASSWORD, is_authenticated

# ========== LOGGER ==========
logger = logging.getLogger("scythe_c2.routes.auth")

router = APIRouter(tags=["auth"])
templates = Jinja2Templates(directory="app/templates")

@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """
    Show hacker-style login page.
    """
    # Jika sudah login, redirect ke dashboard
    token = request.cookies.get("scythe_session")
    if token:
        if is_authenticated(token):
            logger.info("User already logged in, redirecting to dashboard")
            return RedirectResponse(url="/", status_code=302)

    logger.debug("Serving login page")
    return templates.TemplateResponse("login.html", {"request": request})

@router.post("/login")
async def login_process(
    request: Request,
    response: Response,
    password: str = Form(...)
):
    """
    Process login form.
    """
    logger.info(f"Login attempt from {request.client.host}")

    if password == LOGIN_PASSWORD:
        # Create session
        token = create_session(username="admin")
        response = RedirectResponse(url="/", status_code=302)
        response.set_cookie(
            key="scythe_session",
            value=token,
            httponly=True,
            secure=False,  # Set to True if using HTTPS
            samesite="lax",
            max_age=86400  # 24 hours
        )
        logger.info("Login successful")
        return response
    else:
        logger.warning(f"Login failed from {request.client.host}")
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Invalid password. Access Denied."}
        )

@router.post("/logout")
async def logout_process(request: Request, response: Response):
    """
    Logout user - clear session.
    """
    token = request.cookies.get("scythe_session")
    if token:
        delete_session(token)
        logger.info("User logged out")
    response = RedirectResponse(url="/login", status_code=302)
    response.delete_cookie("scythe_session")
    return response

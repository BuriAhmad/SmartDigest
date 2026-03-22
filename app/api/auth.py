"""Authentication endpoints — register, login, logout.

All endpoints set/clear the httpOnly session cookie.
"""

import structlog
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.config import get_settings
from app.services.auth import (
    authenticate_user,
    create_session_token,
    register_user,
)

logger = structlog.get_logger()
router = APIRouter(prefix="/auth", tags=["auth"])

COOKIE_NAME = "sd_session"


def _set_session_cookie(response, token: str) -> None:
    """Set the httpOnly session cookie on a response."""
    settings = get_settings()
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        secure=settings.is_production,
        samesite="lax",
        max_age=72 * 3600,  # 3 days
        path="/",
    )


@router.post("/register")
async def register(request: Request, db: AsyncSession = Depends(get_db)):
    """Register a new user account."""
    form = await request.form()
    email = str(form.get("email", "")).strip().lower()
    password = str(form.get("password", ""))
    name = str(form.get("name", "")).strip()

    # Validate
    errors = []
    if not email or "@" not in email:
        errors.append("Valid email is required")
    if len(password) < 8:
        errors.append("Password must be at least 8 characters")
    if not name:
        errors.append("Name is required")

    if errors:
        return JSONResponse(
            status_code=422,
            content={"detail": "; ".join(errors)},
        )

    try:
        user = await register_user(db, email, password, name)
    except ValueError as exc:
        return JSONResponse(
            status_code=409,
            content={"detail": str(exc)},
        )

    token = create_session_token(user.id, user.email)
    response = RedirectResponse(url="/", status_code=303)
    _set_session_cookie(response, token)
    return response


@router.post("/login")
async def login(request: Request, db: AsyncSession = Depends(get_db)):
    """Log in with email + password."""
    form = await request.form()
    email = str(form.get("email", "")).strip().lower()
    password = str(form.get("password", ""))

    user = await authenticate_user(db, email, password)
    if user is None:
        return JSONResponse(
            status_code=401,
            content={"detail": "Invalid email or password"},
        )

    token = create_session_token(user.id, user.email)
    response = RedirectResponse(url="/", status_code=303)
    _set_session_cookie(response, token)
    return response


@router.post("/logout")
async def logout():
    """Clear the session cookie."""
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie(key=COOKIE_NAME, path="/")
    return response

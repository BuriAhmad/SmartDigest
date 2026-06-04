"""Authentication endpoints — Firebase token exchange and logout."""

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from firebase_admin import auth as firebase_admin_auth
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.config import get_settings
from app.services.auth import (
    create_session_token,
    get_or_create_firebase_user,
    verify_firebase_id_token,
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


@router.post("/firebase/session")
async def create_firebase_session(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Verify a Firebase ID token and set the SmartDigest session cookie."""
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON body") from exc

    id_token = str(payload.get("idToken") or "").strip()
    display_name = str(payload.get("name") or "").strip()
    if not id_token:
        raise HTTPException(status_code=422, detail="Firebase ID token is required")

    try:
        claims = verify_firebase_id_token(id_token)
        user = await get_or_create_firebase_user(db, claims, fallback_name=display_name)
        await db.commit()
    except firebase_admin_auth.InvalidIdTokenError as exc:
        raise HTTPException(status_code=401, detail="Invalid Firebase token") from exc
    except firebase_admin_auth.ExpiredIdTokenError as exc:
        raise HTTPException(status_code=401, detail="Expired Firebase token") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        logger.error("firebase.not_configured", error=str(exc))
        raise HTTPException(status_code=503, detail="Firebase is not configured") from exc
    except Exception as exc:
        logger.error("firebase.session_failed", error=str(exc))
        raise HTTPException(status_code=401, detail="Could not authenticate with Firebase") from exc

    session_token = create_session_token(user.id, user.email)
    response = JSONResponse(
        content={
            "status": "ok",
            "user": {"id": user.id, "email": user.email, "name": user.name},
        }
    )
    _set_session_cookie(response, session_token)
    return response


@router.post("/logout")
async def logout():
    """Clear the session cookie."""
    response = RedirectResponse(url="/login?logged_out=1", status_code=303)
    response.delete_cookie(key=COOKIE_NAME, path="/")
    return response

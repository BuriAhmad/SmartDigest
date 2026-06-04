"""Authentication service — Firebase identity + SmartDigest JWT sessions."""

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Optional

import firebase_admin
import jwt
import structlog
from firebase_admin import auth as firebase_admin_auth
from firebase_admin import credentials
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.user import User

logger = structlog.get_logger()

# JWT configuration
JWT_ALGORITHM = "HS256"
JWT_EXPIRY_HOURS = 72  # 3-day sessions


def create_session_token(user_id: int, email: str) -> str:
    """Create a signed JWT session token."""
    settings = get_settings()
    payload = {
        "sub": user_id,
        "email": email,
        "exp": datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRY_HOURS),
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=JWT_ALGORITHM)


def verify_session_token(token: str) -> Optional[dict]:
    """Decode and verify a JWT session token.

    Returns {"sub": user_id, "email": email} on success, None on failure.
    """
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return {"sub": payload["sub"], "email": payload["email"]}
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
        return None


def get_firebase_app():
    """Initialise and return the Firebase Admin app."""
    try:
        return firebase_admin.get_app()
    except ValueError:
        pass

    settings = get_settings()
    if settings.FIREBASE_SERVICE_ACCOUNT_JSON:
        service_account = json.loads(settings.FIREBASE_SERVICE_ACCOUNT_JSON)
        cred = credentials.Certificate(service_account)
    else:
        key_path = Path(settings.FIREBASE_SERVICE_ACCOUNT_PATH).expanduser()
        if not key_path.exists():
            raise RuntimeError(
                "Firebase service account key not configured. Set "
                "FIREBASE_SERVICE_ACCOUNT_JSON or FIREBASE_SERVICE_ACCOUNT_PATH."
            )
        cred = credentials.Certificate(str(key_path))

    return firebase_admin.initialize_app(cred)


def verify_firebase_id_token(id_token: str) -> dict:
    """Verify a Firebase ID token and return its decoded claims."""
    app = get_firebase_app()
    return firebase_admin_auth.verify_id_token(id_token, app=app)


async def get_or_create_firebase_user(
    db: AsyncSession,
    firebase_claims: dict,
    fallback_name: str = "",
) -> User:
    """Attach a Firebase identity to a local app user, creating one if needed."""
    firebase_uid = str(firebase_claims.get("uid") or "").strip()
    email = _normalise_email(str(firebase_claims.get("email") or ""))
    name = _clean_display_name(
        str(firebase_claims.get("name") or fallback_name or email.split("@")[0])
    )

    if not firebase_uid:
        raise ValueError("Firebase token did not include a user id")
    if not email:
        raise ValueError("Firebase token did not include an email address")

    result = await db.execute(select(User).where(User.firebase_uid == firebase_uid))
    user = result.scalar_one_or_none()

    if user is None:
        result = await db.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()

    if user is None:
        user = User(
            email=email,
            firebase_uid=firebase_uid,
            password_hash=None,
            name=name,
        )
        db.add(user)
        logger.info("user.created_from_firebase", email=email)
    else:
        user.firebase_uid = firebase_uid
        user.email = email
        if name and (not user.name or user.name == user.email):
            user.name = name

    user.last_login_at = datetime.now(timezone.utc)
    await db.flush()
    await db.refresh(user)
    logger.info("user.authenticated_with_firebase", user_id=user.id, email=email)
    return user


def _normalise_email(email: str) -> str:
    return email.strip().lower()


def _clean_display_name(name: str) -> str:
    cleaned = " ".join(name.strip().split())
    return cleaned[:100] or "SmartDigest User"

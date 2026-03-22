"""Authentication service — registration, login, JWT sessions.

Passwords hashed with bcrypt. Sessions issued as JWT in httpOnly cookies.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
import jwt
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.user import User

logger = structlog.get_logger()

# JWT configuration
JWT_ALGORITHM = "HS256"
JWT_EXPIRY_HOURS = 72  # 3-day sessions


def hash_password(plain: str) -> str:
    """Hash a plaintext password with bcrypt."""
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    """Check a plaintext password against a bcrypt hash."""
    return bcrypt.checkpw(plain.encode(), hashed.encode())


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


async def register_user(
    db: AsyncSession,
    email: str,
    password: str,
    name: str,
) -> User:
    """Create a new user account. Raises ValueError if email taken."""
    # Check for existing user
    result = await db.execute(select(User).where(User.email == email))
    if result.scalar_one_or_none() is not None:
        raise ValueError("An account with this email already exists")

    user = User(
        email=email,
        password_hash=hash_password(password),
        name=name,
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)

    logger.info("user.registered", user_id=user.id, email=email)
    return user


async def authenticate_user(
    db: AsyncSession,
    email: str,
    password: str,
) -> Optional[User]:
    """Verify email + password. Returns User on success, None on failure."""
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()

    if user is None or not verify_password(password, user.password_hash):
        logger.warning("auth.login_failed", email=email)
        return None

    # Update last login
    user.last_login_at = datetime.now(timezone.utc)
    logger.info("user.authenticated", user_id=user.id, email=email)
    return user

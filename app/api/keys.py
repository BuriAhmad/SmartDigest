"""API key management endpoints."""

import hashlib
import secrets
from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.api_key import ApiKey
from app.schemas.keys import KeyCreateResponse

logger = structlog.get_logger()
router = APIRouter(prefix="/keys", tags=["keys"])


@router.post("", response_model=KeyCreateResponse, status_code=201)
async def create_key(db: AsyncSession = Depends(get_db)) -> KeyCreateResponse:
    """Issue a new API key.

    Generates a 32-byte random hex token, stores SHA-256 hash,
    and returns the plaintext key exactly once.
    """
    # Generate 32-byte random key → 64-char hex string
    raw_key = secrets.token_hex(32)
    prefix = raw_key[:4]
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()

    api_key = ApiKey(
        prefix=prefix,
        key_hash=key_hash,
    )
    db.add(api_key)
    await db.flush()
    await db.refresh(api_key)

    logger.info("api_key.created", prefix=prefix)

    return KeyCreateResponse(
        key=raw_key,
        prefix=prefix,
        created_at=api_key.created_at,
    )


@router.delete("/{prefix}", status_code=204)
async def revoke_key(
    prefix: str,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Revoke an API key by its prefix (sets revoked_at)."""
    result = await db.execute(
        select(ApiKey).where(
            ApiKey.prefix == prefix,
            ApiKey.revoked_at.is_(None),
        )
    )
    api_key = result.scalar_one_or_none()

    if api_key is None:
        raise HTTPException(status_code=404, detail="Key not found")

    api_key.revoked_at = datetime.now(timezone.utc)
    logger.info("api_key.revoked", prefix=prefix)

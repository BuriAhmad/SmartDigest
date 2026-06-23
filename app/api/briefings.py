"""Briefing CRUD endpoints (formerly 'subscriptions')."""

from datetime import timedelta
from typing import List

import structlog
from arq.connections import create_pool, RedisSettings
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.middleware.rate_limit import limiter
from app.models.briefing import Briefing
from app.models.digest import Digest
from app.schemas.briefings import (
    BriefingCreate,
    BriefingResponse,
    BriefingUpdate,
)

logger = structlog.get_logger()
router = APIRouter(prefix="/briefings", tags=["briefings"])


@router.post("", response_model=BriefingResponse, status_code=201)
async def create_briefing(
    payload: BriefingCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> BriefingResponse:
    """Create a new briefing."""
    _ensure_delivery_email_matches_account(payload.email, request)
    briefing = Briefing(
        user_id=request.state.user_id,
        topic=payload.topic,
        intent_description=payload.intent_description,
        keywords=payload.keywords,
        sources=payload.sources,
        email=payload.email,
        schedule=payload.schedule,
        example_articles=payload.example_articles or [],
        exclusion_keywords=payload.exclusion_keywords or [],
    )
    db.add(briefing)
    await db.flush()
    await db.refresh(briefing)
    logger.info("briefing.created", id=briefing.id, topic=briefing.topic)
    return BriefingResponse.model_validate(briefing)


@router.get("", response_model=List[BriefingResponse])
async def list_briefings(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> List[BriefingResponse]:
    """List all active briefings for the authenticated user."""
    result = await db.execute(
        select(Briefing).where(
            Briefing.user_id == request.state.user_id,
            Briefing.active.is_(True),
        ).order_by(Briefing.created_at.desc())
    )
    briefings = result.scalars().all()
    return [BriefingResponse.model_validate(b) for b in briefings]


@router.get("/{briefing_id}", response_model=BriefingResponse)
async def get_briefing(
    briefing_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> BriefingResponse:
    """Get a single briefing by ID."""
    briefing = await _get_owned_briefing(briefing_id, request, db)
    return BriefingResponse.model_validate(briefing)


@router.patch("/{briefing_id}", response_model=BriefingResponse)
async def update_briefing(
    briefing_id: int,
    payload: BriefingUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> BriefingResponse:
    """Update a briefing (partial update)."""
    briefing = await _get_owned_briefing(briefing_id, request, db)

    update_data = payload.model_dump(exclude_unset=True)
    if "email" in update_data:
        _ensure_delivery_email_matches_account(update_data["email"], request)
    for field, value in update_data.items():
        setattr(briefing, field, value)

    await db.flush()
    await db.refresh(briefing)
    logger.info("briefing.updated", id=briefing.id)
    return BriefingResponse.model_validate(briefing)


@router.delete("/{briefing_id}", status_code=204)
async def delete_briefing(
    briefing_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Soft-delete a briefing (sets active=False)."""
    briefing = await _get_owned_briefing(briefing_id, request, db)
    briefing.active = False
    logger.info("briefing.soft_deleted", id=briefing.id)


@router.post("/{briefing_id}/trigger", status_code=202)
@limiter.limit("3/hour")
async def trigger_pipeline(
    briefing_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Trigger the pipeline for a briefing via ARQ.

    Create the digest row before enqueueing so manual runs appear in history even
    while the worker is still waiting to pick up the job.
    """
    briefing = await _get_owned_briefing(briefing_id, request, db)
    if not briefing.active:
        raise HTTPException(status_code=404, detail="Briefing is inactive")
    _ensure_delivery_email_matches_account(briefing.email, request)

    digest = Digest(briefing_id=briefing.id, status="queued")
    db.add(digest)
    await db.flush()
    await db.refresh(digest)
    settings = get_settings()
    job_id = f"digest:{digest.id}"

    redis = None
    try:
        redis = await create_pool(RedisSettings.from_dsn(settings.REDIS_URL))
        job = await redis.enqueue_job(
            "run_pipeline",
            briefing_id,
            digest.id,
            _job_id=job_id,
            _expires=timedelta(seconds=settings.ARQ_JOB_EXPIRES_SECONDS),
        )
        if job is None:
            raise RuntimeError("ARQ did not accept the job")
        logger.info(
            "pipeline.triggered",
            briefing_id=briefing_id,
            digest_id=digest.id,
            job_id=job_id,
        )
    except Exception as exc:
        logger.error("pipeline.enqueue_failed", error=str(exc))
        raise HTTPException(status_code=503, detail="Could not enqueue job — Redis unavailable")
    finally:
        if redis is not None:
            await redis.close()

    return {
        "job_id": job_id,
        "digest_id": digest.id,
        "status": "queued",
        "message": "Run queued. It will appear in history now and process when the worker is running.",
    }


def _normalise_email(email: str) -> str:
    return email.strip().lower()


def _ensure_delivery_email_matches_account(email: str, request: Request) -> None:
    """Only allow delivery to the authenticated account email.

    Digests are displayed by account ownership. Allowing arbitrary delivery
    recipients means a digest can be emailed to one person while being visible
    only in another user's Digests page.
    """
    account_email = getattr(request.state, "user_email", "")
    if _normalise_email(email) != _normalise_email(account_email):
        raise HTTPException(
            status_code=400,
            detail="Delivery email must match your account email.",
        )


async def _get_owned_briefing(
    briefing_id: int,
    request: Request,
    db: AsyncSession,
) -> Briefing:
    """Fetch a briefing, ensuring it belongs to the caller. Returns 404 if not."""
    result = await db.execute(
        select(Briefing).where(
            Briefing.id == briefing_id,
            Briefing.user_id == request.state.user_id,
        )
    )
    briefing = result.scalar_one_or_none()
    if briefing is None:
        raise HTTPException(status_code=404, detail="Briefing not found")
    return briefing

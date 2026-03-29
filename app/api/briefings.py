"""Briefing CRUD endpoints (formerly 'subscriptions')."""

import uuid
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
    """Trigger the pipeline for a briefing via ARQ."""
    briefing = await _get_owned_briefing(briefing_id, request, db)
    if not briefing.active:
        raise HTTPException(status_code=404, detail="Briefing is inactive")

    settings = get_settings()
    job_id = f"arq:job:{uuid.uuid4().hex[:12]}"

    try:
        redis = await create_pool(RedisSettings.from_dsn(settings.REDIS_URL))
        job = await redis.enqueue_job(
            "run_pipeline",
            briefing_id,
            _job_id=job_id,
        )
        await redis.close()
        logger.info("pipeline.triggered", briefing_id=briefing_id, job_id=job_id)
    except Exception as exc:
        logger.error("pipeline.enqueue_failed", error=str(exc))
        raise HTTPException(status_code=503, detail="Could not enqueue job — Redis unavailable")

    return {"job_id": job_id, "status": "queued"}


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

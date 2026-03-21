"""Subscription CRUD endpoints."""

import uuid
from typing import List

import structlog
from arq.connections import ArqRedis, create_pool, RedisSettings
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.middleware.rate_limit import limiter
from app.models.subscription import Subscription
from app.schemas.subscriptions import (
    SubscriptionCreate,
    SubscriptionResponse,
    SubscriptionUpdate,
)

logger = structlog.get_logger()
router = APIRouter(prefix="/subscriptions", tags=["subscriptions"])


@router.post("", response_model=SubscriptionResponse, status_code=201)
async def create_subscription(
    payload: SubscriptionCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> SubscriptionResponse:
    """Create a new subscription."""
    sub = Subscription(
        api_key_id=request.state.owner_key_id,
        topic=payload.topic,
        sources=payload.sources,
        email=payload.email,
        schedule=payload.schedule,
    )
    db.add(sub)
    await db.flush()
    await db.refresh(sub)
    logger.info("subscription.created", id=sub.id, topic=sub.topic)
    return SubscriptionResponse.model_validate(sub)


@router.get("", response_model=List[SubscriptionResponse])
async def list_subscriptions(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> List[SubscriptionResponse]:
    """List all active subscriptions for the authenticated key owner."""
    result = await db.execute(
        select(Subscription).where(
            Subscription.api_key_id == request.state.owner_key_id,
            Subscription.active.is_(True),
        ).order_by(Subscription.created_at.desc())
    )
    subs = result.scalars().all()
    return [SubscriptionResponse.model_validate(s) for s in subs]


@router.get("/{subscription_id}", response_model=SubscriptionResponse)
async def get_subscription(
    subscription_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> SubscriptionResponse:
    """Get a single subscription by ID."""
    sub = await _get_owned_subscription(subscription_id, request, db)
    return SubscriptionResponse.model_validate(sub)


@router.patch("/{subscription_id}", response_model=SubscriptionResponse)
async def update_subscription(
    subscription_id: int,
    payload: SubscriptionUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> SubscriptionResponse:
    """Update a subscription (partial update)."""
    sub = await _get_owned_subscription(subscription_id, request, db)

    update_data = payload.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(sub, field, value)

    await db.flush()
    await db.refresh(sub)
    logger.info("subscription.updated", id=sub.id)
    return SubscriptionResponse.model_validate(sub)


@router.delete("/{subscription_id}", status_code=204)
async def delete_subscription(
    subscription_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Soft-delete a subscription (sets active=False)."""
    sub = await _get_owned_subscription(subscription_id, request, db)
    sub.active = False
    logger.info("subscription.soft_deleted", id=sub.id)


@router.post("/{subscription_id}/trigger", status_code=202)
@limiter.limit("3/hour")
async def trigger_pipeline(
    subscription_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Trigger the pipeline for a subscription via ARQ."""
    sub = await _get_owned_subscription(subscription_id, request, db)
    if not sub.active:
        raise HTTPException(status_code=404, detail="Subscription is inactive")

    settings = get_settings()
    redis_url = settings.REDIS_URL.replace("redis://", "")
    parts = redis_url.split(":")
    host = parts[0] if parts[0] else "localhost"
    port = int(parts[1]) if len(parts) > 1 else 6379

    job_id = f"arq:job:{uuid.uuid4().hex[:12]}"

    try:
        redis = await create_pool(RedisSettings(host=host, port=port))
        job = await redis.enqueue_job(
            "run_pipeline",
            subscription_id,
            _job_id=job_id,
        )
        await redis.close()
        logger.info("pipeline.triggered", subscription_id=subscription_id, job_id=job_id)
    except Exception as exc:
        logger.error("pipeline.enqueue_failed", error=str(exc))
        raise HTTPException(status_code=503, detail="Could not enqueue job — Redis unavailable")

    return {"job_id": job_id, "status": "queued"}


async def _get_owned_subscription(
    subscription_id: int,
    request: Request,
    db: AsyncSession,
) -> Subscription:
    """Fetch a subscription, ensuring it belongs to the caller. Returns 404 if not."""
    result = await db.execute(
        select(Subscription).where(
            Subscription.id == subscription_id,
            Subscription.api_key_id == request.state.owner_key_id,
        )
    )
    sub = result.scalar_one_or_none()
    if sub is None:
        raise HTTPException(status_code=404, detail="Subscription not found")
    return sub

"""Digest endpoints — real implementation.

List digests and view digest details with items.
"""

import structlog
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select, func as sqlfunc
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.digest import Digest
from app.models.digest_item import DigestItem
from app.models.subscription import Subscription

logger = structlog.get_logger()
router = APIRouter(prefix="/digests", tags=["digests"])


@router.get("")
async def list_digests(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> list:
    """List digests for the authenticated user's subscriptions."""
    key_id = getattr(request.state, "owner_key_id", None)
    if key_id is None:
        return []

    # Get digests via subscription ownership
    result = await db.execute(
        select(
            Digest.id,
            Digest.subscription_id,
            Digest.status,
            Digest.created_at,
            Digest.delivered_at,
            Subscription.topic,
            sqlfunc.count(DigestItem.id).label("item_count"),
        )
        .join(Subscription, Digest.subscription_id == Subscription.id)
        .outerjoin(DigestItem, DigestItem.digest_id == Digest.id)
        .where(Subscription.api_key_id == key_id)
        .group_by(
            Digest.id,
            Digest.subscription_id,
            Digest.status,
            Digest.created_at,
            Digest.delivered_at,
            Subscription.topic,
        )
        .order_by(Digest.created_at.desc())
        .limit(50)
    )
    rows = result.all()

    return [
        {
            "id": row[0],
            "subscription_id": row[1],
            "status": row[2],
            "created_at": row[3].isoformat() if row[3] else None,
            "delivered_at": row[4].isoformat() if row[4] else None,
            "topic": row[5],
            "item_count": row[6],
        }
        for row in rows
    ]


@router.get("/{digest_id}")
async def get_digest(
    digest_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Get a single digest with its items."""
    result = await db.execute(
        select(Digest).where(Digest.id == digest_id)
    )
    digest = result.scalar_one_or_none()

    if digest is None:
        raise HTTPException(status_code=404, detail="Digest not found")

    # Get items
    items_result = await db.execute(
        select(DigestItem).where(DigestItem.digest_id == digest_id)
    )
    items = items_result.scalars().all()

    # Get topic from subscription
    sub_result = await db.execute(
        select(Subscription.topic).where(Subscription.id == digest.subscription_id)
    )
    topic_row = sub_result.first()

    return {
        "id": digest.id,
        "subscription_id": digest.subscription_id,
        "status": digest.status,
        "created_at": digest.created_at.isoformat() if digest.created_at else None,
        "delivered_at": digest.delivered_at.isoformat() if digest.delivered_at else None,
        "topic": topic_row[0] if topic_row else "Unknown",
        "items": [
            {
                "id": item.id,
                "title": item.title,
                "item_url": item.item_url,
                "source_url": item.source_url,
                "summary": item.summary,
                "fetch_duration_ms": item.fetch_duration_ms,
                "published_at": item.published_at.isoformat() if item.published_at else None,
            }
            for item in items
        ],
    }

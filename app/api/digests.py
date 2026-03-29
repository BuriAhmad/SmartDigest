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
from app.models.briefing import Briefing

logger = structlog.get_logger()
router = APIRouter(prefix="/digests", tags=["digests"])


@router.get("")
async def list_digests(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> list:
    """List digests for the authenticated user's briefings."""
    user_id = getattr(request.state, "user_id", None)
    if user_id is None:
        return []

    # Get digests via briefing ownership
    result = await db.execute(
        select(
            Digest.id,
            Digest.briefing_id,
            Digest.status,
            Digest.created_at,
            Digest.delivered_at,
            Briefing.topic,
            sqlfunc.count(DigestItem.id).label("item_count"),
        )
        .join(Briefing, Digest.briefing_id == Briefing.id)
        .outerjoin(DigestItem, DigestItem.digest_id == Digest.id)
        .where(Briefing.user_id == user_id)
        .group_by(
            Digest.id,
            Digest.briefing_id,
            Digest.status,
            Digest.created_at,
            Digest.delivered_at,
            Briefing.topic,
        )
        .order_by(Digest.created_at.desc())
        .limit(50)
    )
    rows = result.all()

    return [
        {
            "id": row[0],
            "briefing_id": row[1],
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
    """Get a single digest with its items. Only accessible to the owning user."""
    user_id = getattr(request.state, "user_id", None)

    # Join through briefing to enforce ownership
    result = await db.execute(
        select(Digest)
        .join(Briefing, Digest.briefing_id == Briefing.id)
        .where(
            Digest.id == digest_id,
            Briefing.user_id == user_id,
        )
    )
    digest = result.scalar_one_or_none()

    if digest is None:
        raise HTTPException(status_code=404, detail="Digest not found")

    # Get items
    items_result = await db.execute(
        select(DigestItem).where(DigestItem.digest_id == digest_id)
    )
    items = items_result.scalars().all()

    # Get topic from briefing
    briefing_result = await db.execute(
        select(Briefing.topic).where(Briefing.id == digest.briefing_id)
    )
    topic_row = briefing_result.first()

    return {
        "id": digest.id,
        "briefing_id": digest.briefing_id,
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
                "heuristic_score": item.heuristic_score,
                "llm_relevance_score": item.llm_relevance_score,
                "llm_relevance_reason": item.llm_relevance_reason,
            }
            for item in items
        ],
    }

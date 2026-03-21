"""Curated sources endpoint."""

from typing import List

import structlog
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.curated_source import CuratedSource
from app.schemas.subscriptions import SourceResponse

logger = structlog.get_logger()
router = APIRouter(prefix="/sources", tags=["sources"])


@router.get("", response_model=List[SourceResponse])
async def list_sources(db: AsyncSession = Depends(get_db)) -> List[SourceResponse]:
    """Return all active curated RSS sources."""
    result = await db.execute(
        select(CuratedSource)
        .where(CuratedSource.active.is_(True))
        .order_by(CuratedSource.name)
    )
    sources = result.scalars().all()
    return [SourceResponse.model_validate(s) for s in sources]

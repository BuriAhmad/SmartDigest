"""Metrics endpoints — real implementation.

Pipeline health and per-key usage from pipeline_events aggregates.
"""

import structlog
from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services.metrics import get_pipeline_metrics, get_usage_metrics

logger = structlog.get_logger()
router = APIRouter(prefix="/metrics", tags=["metrics"])


@router.get("/pipeline")
async def pipeline_metrics(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Pipeline metrics from pipeline_events aggregates (last 24h)."""
    return await get_pipeline_metrics(db, period_hours=24)


@router.get("/usage")
async def usage_metrics(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Per-key usage metrics."""
    key_id = getattr(request.state, "owner_key_id", None)
    if key_id is None:
        return {
            "key_prefix": "????",
            "total_api_calls": 0,
            "last_used_at": None,
            "subscription_count": 0,
            "digest_count": 0,
        }
    return await get_usage_metrics(db, key_id)

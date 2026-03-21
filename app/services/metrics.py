"""MetricsService — real SQL aggregates from pipeline_events.

Provides data for the dashboard health panel and /api/v1/metrics endpoints.
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import structlog
from sqlalchemy import func as sqlfunc, select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.pipeline_event import PipelineEvent
from app.models.digest import Digest
from app.models.subscription import Subscription
from app.models.api_key import ApiKey

logger = structlog.get_logger()


async def get_pipeline_metrics(
    session: AsyncSession,
    period_hours: int = 24,
) -> Dict[str, Any]:
    """Aggregate pipeline_events for the last N hours.

    Returns dict with: total_jobs, by_status, stage_avg_ms, last_error.
    """
    since = datetime.now(timezone.utc) - timedelta(hours=period_hours)

    # Total completed events (success + failed)
    total_result = await session.execute(
        select(sqlfunc.count(PipelineEvent.id)).where(
            PipelineEvent.created_at >= since,
            PipelineEvent.status.in_(["success", "failed"]),
        )
    )
    total_jobs = total_result.scalar() or 0

    # By status
    done_result = await session.execute(
        select(sqlfunc.count(PipelineEvent.id)).where(
            PipelineEvent.created_at >= since,
            PipelineEvent.status == "success",
        )
    )
    done = done_result.scalar() or 0

    failed_result = await session.execute(
        select(sqlfunc.count(PipelineEvent.id)).where(
            PipelineEvent.created_at >= since,
            PipelineEvent.status == "failed",
        )
    )
    failed = failed_result.scalar() or 0

    # Average duration per stage (only successful events with duration)
    stage_avg = {}
    for stage in ["fetch", "summarise", "deliver"]:
        avg_result = await session.execute(
            select(sqlfunc.avg(PipelineEvent.duration_ms)).where(
                PipelineEvent.created_at >= since,
                PipelineEvent.stage == stage,
                PipelineEvent.status == "success",
                PipelineEvent.duration_ms.isnot(None),
            )
        )
        avg_val = avg_result.scalar()
        stage_avg[stage] = int(avg_val) if avg_val else 0

    # Last error
    last_error_result = await session.execute(
        select(PipelineEvent).where(
            PipelineEvent.status == "failed",
        ).order_by(desc(PipelineEvent.created_at)).limit(1)
    )
    last_error_row = last_error_result.scalar_one_or_none()

    last_error = None
    if last_error_row:
        last_error = {
            "stage": last_error_row.stage,
            "msg": last_error_row.error_msg or "Unknown error",
            "at": last_error_row.created_at.strftime("%b %d, %H:%M UTC") if last_error_row.created_at else "—",
        }

    return {
        "period_hours": period_hours,
        "total_jobs": total_jobs,
        "by_status": {"done": done, "failed": failed},
        "stage_avg_ms": stage_avg,
        "last_error": last_error,
    }


async def get_usage_metrics(
    session: AsyncSession,
    key_id: int,
) -> Dict[str, Any]:
    """Per-key usage metrics."""

    # Get key info
    key_result = await session.execute(
        select(ApiKey).where(ApiKey.id == key_id)
    )
    key = key_result.scalar_one_or_none()

    if not key:
        return {
            "key_prefix": "????",
            "total_api_calls": 0,
            "last_used_at": None,
            "subscription_count": 0,
            "digest_count": 0,
        }

    # Count active subscriptions
    sub_count_result = await session.execute(
        select(sqlfunc.count(Subscription.id)).where(
            Subscription.api_key_id == key_id,
            Subscription.active.is_(True),
        )
    )
    sub_count = sub_count_result.scalar() or 0

    # Count digests from these subscriptions
    digest_count_result = await session.execute(
        select(sqlfunc.count(Digest.id)).where(
            Digest.subscription_id.in_(
                select(Subscription.id).where(
                    Subscription.api_key_id == key_id,
                )
            )
        )
    )
    digest_count = digest_count_result.scalar() or 0

    return {
        "key_prefix": key.prefix,
        "total_api_calls": key.api_call_count or 0,
        "last_used_at": key.last_used_at.isoformat() if key.last_used_at else None,
        "subscription_count": sub_count,
        "digest_count": digest_count,
    }

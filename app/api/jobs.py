"""Job status endpoint backed by digest state and the ARQ Redis queue."""

import structlog
from fastapi import APIRouter, Depends, Request
import redis.asyncio as redis
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional

from app.config import get_settings
from app.database import get_db
from app.models.digest import Digest
from app.models.pipeline_event import PipelineEvent

logger = structlog.get_logger()
router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("/{job_id}")
async def get_job_status(
    job_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Check digest job status using DB state plus Redis queue presence."""
    digest_id = _digest_id_from_job_id(job_id)
    response = {
        "job_id": job_id,
        "status": "unknown",
        "digest_id": digest_id,
        "queue_status": "unknown",
        "result": None,
    }

    if digest_id is not None:
        digest = await db.get(Digest, digest_id)
        if digest is not None:
            response["status"] = digest.status
            response["created_at"] = (
                digest.created_at.isoformat() if digest.created_at else None
            )
            response["delivered_at"] = (
                digest.delivered_at.isoformat() if digest.delivered_at else None
            )
            event_count = await db.scalar(
                select(func.count(PipelineEvent.id)).where(
                    PipelineEvent.digest_id == digest.id,
                )
            )
            latest_event = (
                await db.execute(
                    select(PipelineEvent)
                    .where(PipelineEvent.digest_id == digest.id)
                    .order_by(desc(PipelineEvent.created_at))
                    .limit(1)
                )
            ).scalar_one_or_none()
            response["pipeline_event_count"] = event_count or 0
            response["latest_event"] = (
                {
                    "stage": latest_event.stage,
                    "status": latest_event.status,
                    "error_msg": latest_event.error_msg,
                    "created_at": (
                        latest_event.created_at.isoformat()
                        if latest_event.created_at
                        else None
                    ),
                }
                if latest_event
                else None
            )
        else:
            response["status"] = "missing"

    settings = get_settings()
    client = redis.from_url(settings.REDIS_URL)
    try:
        queue_score = await client.zscore("arq:queue", job_id)
        job_exists = bool(await client.exists(f"arq:job:{job_id}"))
        if queue_score is not None:
            response["queue_status"] = "queued"
        elif job_exists:
            response["queue_status"] = "stored"
        else:
            response["queue_status"] = "not_found"
    except Exception as exc:
        logger.warning("jobs.redis_status_failed", job_id=job_id, error=str(exc))
        response["queue_status"] = "unavailable"
        response["queue_error"] = str(exc)
    finally:
        await client.aclose()

    return response


def _digest_id_from_job_id(job_id: str) -> Optional[int]:
    if not job_id.startswith("digest:"):
        return None
    digest_id = job_id.split(":", 2)[1]
    if not digest_id.isdigit():
        return None
    return int(digest_id)

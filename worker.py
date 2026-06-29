"""ARQ worker entrypoint.

Run with: python worker.py
Includes cron job for scheduled digest delivery at 06:00 UTC daily.
"""

import asyncio

import structlog
from arq import cron, run_worker
from arq.connections import RedisSettings

from app.config import get_settings
from app.services.filters.reranker import warm_reranker_model
from app.services.filters.semantic import warm_semantic_model
from app.services.scheduler import (
    enqueue_scheduled_digests,
    recover_queued_digests,
    run_pipeline,
)

settings = get_settings()
logger = structlog.get_logger()


class WorkerSettings:
    """ARQ worker configuration."""

    # RedisSettings.from_dsn() handles all URL formats including:
    # redis://host:port (simple local)
    # redis://default:password@host:port (Railway-style with credentials)
    # rediss://... (SSL)
    redis_settings = RedisSettings.from_dsn(settings.REDIS_URL)
    functions = [run_pipeline]
    job_timeout = settings.ARQ_JOB_TIMEOUT_SECONDS
    max_tries = settings.ARQ_MAX_TRIES
    max_jobs = settings.ARQ_MAX_JOBS

    # Check every 30 minutes; enqueue_scheduled_digests matches briefing.schedule.
    cron_jobs = [
        cron(
            recover_queued_digests,
            minute={0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55},
            run_at_startup=True,
        ),
        cron(
            enqueue_scheduled_digests,
            minute={0, 30},
            run_at_startup=True,
        ),
    ]


async def _preload_enabled_models() -> None:
    if settings.is_production and settings.SEMANTIC_RETRIEVAL_ENABLED:
        warmed = await warm_semantic_model(settings.SEMANTIC_MODEL_NAME)
        logger.info(
            "semantic.model_ready",
            model_name=settings.SEMANTIC_MODEL_NAME,
            warmed=warmed,
        )

    if settings.is_production and settings.RERANKER_ENABLED:
        warmed = await warm_reranker_model(settings.RERANKER_MODEL_NAME)
        logger.info(
            "reranker.model_ready",
            model_name=settings.RERANKER_MODEL_NAME,
            warmed=warmed,
        )


if __name__ == "__main__":
    asyncio.run(_preload_enabled_models())
    run_worker(WorkerSettings)

"""ARQ worker entrypoint.

Run with: python worker.py
Includes cron job for scheduled digest delivery at 06:00 UTC daily.
"""

from arq import cron, run_worker
from arq.connections import RedisSettings

from app.config import get_settings
from app.services.scheduler import (
    enqueue_scheduled_digests,
    recover_queued_digests,
    run_pipeline,
)

settings = get_settings()


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


if __name__ == "__main__":
    run_worker(WorkerSettings)

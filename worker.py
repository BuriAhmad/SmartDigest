"""ARQ worker entrypoint.

Run with: python worker.py
Includes cron job for scheduled digest delivery at 06:00 UTC daily.
"""

from arq import cron, run_worker
from arq.connections import RedisSettings

from app.config import get_settings
from app.services.scheduler import run_pipeline, enqueue_scheduled_digests

settings = get_settings()


class WorkerSettings:
    """ARQ worker configuration."""

    # RedisSettings.from_dsn() handles all URL formats including:
    # redis://host:port (simple local)
    # redis://default:password@host:port (Railway-style with credentials)
    # rediss://... (SSL)
    redis_settings = RedisSettings.from_dsn(settings.REDIS_URL)
    functions = [run_pipeline]

    # Daily cron at 06:00 UTC — enqueue digest pipelines for all active briefings
    cron_jobs = [
        cron(
            enqueue_scheduled_digests,
            hour={6},
            minute={0},
            run_at_startup=False,
        ),
    ]


if __name__ == "__main__":
    run_worker(WorkerSettings)

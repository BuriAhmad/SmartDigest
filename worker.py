"""ARQ worker entrypoint.

Run with: python worker.py
Includes cron job for scheduled digest delivery at 06:00 UTC daily.
"""

from arq import cron, run_worker
from arq.connections import RedisSettings

from app.config import get_settings
from app.services.scheduler import run_pipeline, enqueue_scheduled_digests

settings = get_settings()


def _parse_redis_url(url: str) -> RedisSettings:
    """Parse redis://host:port into RedisSettings."""
    # url format: redis://host:port
    url = url.replace("redis://", "")
    parts = url.split(":")
    host = parts[0] if parts[0] else "localhost"
    port = int(parts[1]) if len(parts) > 1 else 6379
    return RedisSettings(host=host, port=port)


class WorkerSettings:
    """ARQ worker configuration."""

    redis_settings = _parse_redis_url(settings.REDIS_URL)
    functions = [run_pipeline]

    # Daily cron at 06:00 UTC — enqueue digest pipelines for all active subscriptions
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

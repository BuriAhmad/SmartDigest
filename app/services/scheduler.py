"""Scheduler — ARQ job functions for the full pipeline.

worker.py imports from here. Pipeline: fetch → summarise → deliver.
Also exports enqueue_scheduled_digests for the ARQ cron job.
"""

import structlog
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from app.database import async_session
from app.models.subscription import Subscription
from app.models.digest import Digest
from app.models.digest_item import DigestItem
from app.models.pipeline_event import PipelineEvent
from app.services.fetcher import fetch_articles
from app.services.summariser import summarise_articles
from app.services.mailer import send_digest_email

logger = structlog.get_logger()


async def run_pipeline(ctx: dict, subscription_id: int) -> dict:
    """Full pipeline job: fetch → summarise → deliver.

    Creates digest + digest_items, sends email with AI-generated summaries.
    Tracks every stage in pipeline_events for observability.
    """
    log = logger.bind(subscription_id=subscription_id)
    log.info("pipeline.started")

    async with async_session() as session:
        # ── Load subscription ─────────────────────────────────────
        result = await session.execute(
            select(Subscription).where(Subscription.id == subscription_id)
        )
        sub = result.scalar_one_or_none()

        if sub is None or not sub.active:
            log.warning("pipeline.subscription_not_found")
            return {"status": "failed", "error": "Subscription not found or inactive"}

        # ── Create digest record ──────────────────────────────────
        digest = Digest(subscription_id=sub.id, status="processing")
        session.add(digest)
        await session.flush()
        await session.refresh(digest)
        digest_id = digest.id
        log = log.bind(digest_id=digest_id)

        # ── STAGE 1: FETCH ────────────────────────────────────────
        session.add(PipelineEvent(
            digest_id=digest_id, stage="fetch", status="started",
        ))
        await session.flush()

        try:
            articles, fetch_ms = await fetch_articles(
                sources=sub.sources,
                topic=sub.topic,
            )

            if not articles:
                session.add(PipelineEvent(
                    digest_id=digest_id, stage="fetch", status="failed",
                    duration_ms=fetch_ms, error_msg="No articles fetched",
                    item_count=0,
                ))
                digest.status = "failed"
                await session.commit()
                log.warning("pipeline.no_articles", duration_ms=fetch_ms)
                return {"status": "failed", "error": "No articles fetched"}

            session.add(PipelineEvent(
                digest_id=digest_id, stage="fetch", status="success",
                duration_ms=fetch_ms, item_count=len(articles),
            ))
            await session.flush()
            log.info("pipeline.fetch_done", articles=len(articles), duration_ms=fetch_ms)

        except Exception as exc:
            session.add(PipelineEvent(
                digest_id=digest_id, stage="fetch", status="failed",
                error_msg=str(exc),
            ))
            digest.status = "failed"
            await session.commit()
            log.error("pipeline.fetch_error", error=str(exc))
            return {"status": "failed", "error": str(exc)}

        # ── STAGE 2: SUMMARISE ────────────────────────────────────
        session.add(PipelineEvent(
            digest_id=digest_id, stage="summarise", status="started",
        ))
        await session.flush()

        summarise_start_ms = _now_ms()
        try:
            articles = await summarise_articles(articles, sub.topic)
            summarise_ms = _now_ms() - summarise_start_ms

            session.add(PipelineEvent(
                digest_id=digest_id, stage="summarise", status="success",
                duration_ms=summarise_ms, item_count=len(articles),
            ))
            await session.flush()
            log.info("pipeline.summarise_done", articles=len(articles), duration_ms=summarise_ms)

        except Exception as exc:
            summarise_ms = _now_ms() - summarise_start_ms
            session.add(PipelineEvent(
                digest_id=digest_id, stage="summarise", status="failed",
                duration_ms=summarise_ms, error_msg=str(exc),
            ))
            await session.flush()
            log.error("pipeline.summarise_error", error=str(exc))
            # Continue with unsummarised articles — still deliver what we have
            for article in articles:
                if not article.get("summary"):
                    article["summary"] = "[Summary unavailable]"

        # ── SAVE DIGEST ITEMS ─────────────────────────────────────
        for article in articles:
            item = DigestItem(
                digest_id=digest_id,
                source_url=article.get("source_url", ""),
                title=article.get("title", "Untitled"),
                item_url=article.get("url", ""),
                raw_content=article.get("raw_content", ""),
                summary=article.get("summary", "[Summary unavailable]"),
                fetch_duration_ms=fetch_ms,
                published_at=article.get("published_at"),
            )
            session.add(item)
        await session.flush()

        # ── STAGE 3: DELIVER ──────────────────────────────────────
        session.add(PipelineEvent(
            digest_id=digest_id, stage="deliver", status="started",
        ))
        await session.flush()

        try:
            html_body = _build_digest_email(sub.topic, sub.email, articles)

            start_ms = _now_ms()
            success = await send_digest_email(
                to_email=sub.email,
                subject=f"SmartDigest: {sub.topic}",
                html_body=html_body,
            )
            deliver_ms = _now_ms() - start_ms

            if success:
                digest.status = "delivered"
                digest.delivered_at = datetime.now(timezone.utc)
                session.add(PipelineEvent(
                    digest_id=digest_id, stage="deliver", status="success",
                    duration_ms=deliver_ms,
                ))
                log.info("pipeline.delivered", duration_ms=deliver_ms)
            else:
                digest.status = "failed"
                session.add(PipelineEvent(
                    digest_id=digest_id, stage="deliver", status="failed",
                    duration_ms=deliver_ms, error_msg="Email delivery failed",
                ))
                log.error("pipeline.delivery_failed")

        except Exception as exc:
            digest.status = "failed"
            session.add(PipelineEvent(
                digest_id=digest_id, stage="deliver", status="failed",
                error_msg=str(exc),
            ))
            log.error("pipeline.deliver_error", error=str(exc))

        await session.commit()

    return {"status": digest.status, "digest_id": digest_id}


async def enqueue_scheduled_digests(ctx: dict) -> dict:
    """ARQ cron job — enqueues pipelines for subscriptions due now.

    Checks which active subscriptions have a matching cron schedule.
    For simplicity, we trigger ALL active subscriptions (the schedule field
    is informational for the MVP — a full cron matcher would be Phase 7).
    """
    from arq.connections import create_pool, RedisSettings
    from app.config import get_settings

    log = logger.bind(job="enqueue_scheduled_digests")
    log.info("cron.checking_subscriptions")

    settings = get_settings()

    async with async_session() as session:
        result = await session.execute(
            select(Subscription).where(Subscription.active.is_(True))
        )
        subs = result.scalars().all()

    if not subs:
        log.info("cron.no_active_subscriptions")
        return {"enqueued": 0}

    redis = await create_pool(RedisSettings.from_dsn(settings.REDIS_URL))
    enqueued = 0
    for sub in subs:
        try:
            await redis.enqueue_job("run_pipeline", sub.id)
            enqueued += 1
        except Exception as exc:
            log.error("cron.enqueue_failed", subscription_id=sub.id, error=str(exc))
    await redis.close()

    log.info("cron.enqueued", count=enqueued)
    return {"enqueued": enqueued}


def _build_digest_email(topic: str, email: str, articles: list) -> str:
    """Build a beautiful HTML email with article summaries."""
    items_html = ""
    for article in articles:
        title = article.get("title", "Untitled")
        url = article.get("url", "#")
        summary = article.get("summary", "[Summary unavailable]")
        source = article.get("source_url", "").replace("https://", "").replace("http://", "")
        if len(source) > 40:
            source = source[:40] + "..."

        items_html += f"""
        <div style="margin-bottom: 20px; padding: 16px; background: #f9fafb; border-radius: 8px; border: 1px solid #e5e7eb;">
            <a href="{url}" style="color: #2563eb; text-decoration: none; font-weight: 600; font-size: 15px;">{title}</a>
            <div style="color: #9ca3af; font-size: 12px; margin: 4px 0 8px;">{source}</div>
            <p style="color: #374151; font-size: 14px; line-height: 1.5; margin: 0;">{summary}</p>
        </div>
        """

    return f"""
    <html>
    <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px; background: #ffffff;">
        <div style="text-align: center; margin-bottom: 24px;">
            <h1 style="color: #1f2937; margin: 0;">📰 SmartDigest</h1>
            <p style="color: #6b7280; font-size: 14px;">Your AI-curated summary for <strong>{topic}</strong></p>
        </div>

        <div style="background: #eff6ff; border-radius: 8px; padding: 12px 16px; margin-bottom: 24px; text-align: center;">
            <span style="color: #1d4ed8; font-size: 14px;">📊 {len(articles)} articles summarised by AI</span>
        </div>

        {items_html}

        <hr style="border: none; border-top: 1px solid #e5e7eb; margin: 24px 0;">
        <p style="color: #9ca3af; font-size: 12px; text-align: center;">
            Sent by SmartDigest to {email}<br>
            Powered by Gemini 2.0 Flash
        </p>
    </body>
    </html>
    """


def _now_ms() -> int:
    """Current time in milliseconds."""
    return int(datetime.now(timezone.utc).timestamp() * 1000)

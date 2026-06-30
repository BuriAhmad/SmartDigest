"""Scheduler — ARQ job functions for the full pipeline.

worker.py imports from here. Pipeline: fetch → filter → summarise → deliver.
Also exports enqueue_scheduled_digests for the ARQ cron job.
"""

import structlog
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from arq import Retry
from sqlalchemy import func, select
from arq.connections import create_pool, RedisSettings
from app.config import get_settings
from app.database import async_session
from app.models.briefing import Briefing
from app.models.curated_source import CuratedSource
from app.models.digest import Digest
from app.models.digest_item import DigestItem
from app.models.pipeline_event import PipelineEvent
from app.models.user import User
from app.services.fetcher import fetch_articles
from app.services.summariser import summarise_articles
from app.services.mailer import send_digest_email
from app.services.intent import build_intent_context, build_semantic_query, extract_all_keywords
from app.services.filters import FilterPipeline
from app.services.llm import LLMRetryableError

logger = structlog.get_logger()

_PIPELINE_LOCK_NAMESPACE = 0x5D16E57


async def _load_source_metadata(session, source_urls: List[str]) -> Dict[str, Dict]:
    """Load scraper dispatch data for the sources selected by a briefing."""
    if not source_urls:
        return {}

    result = await session.execute(
        select(CuratedSource).where(CuratedSource.url.in_(source_urls))
    )
    return {
        source.url: {
            "name": source.name,
            "source_type": source.source_type,
            "scraper_config": source.scraper_config or {},
        }
        for source in result.scalars().all()
    }


async def run_pipeline(ctx: dict, briefing_id: int, digest_id: Optional[int] = None) -> dict:
    """Full pipeline job: fetch → filter → summarise → deliver.

    Creates digest + digest_items, sends email with AI-generated summaries.
    Tracks every stage in pipeline_events for observability.
    """
    log = logger.bind(briefing_id=briefing_id)
    log.info("pipeline.started")

    async with async_session() as session:
        # ── Load briefing ─────────────────────────────────────────
        result = await session.execute(
            select(Briefing).where(Briefing.id == briefing_id)
        )
        briefing = result.scalar_one_or_none()

        if briefing is None or not briefing.active:
            await _mark_digest_not_started(
                session,
                digest_id,
                "failed",
                "Briefing not found or inactive",
            )
            log.warning("pipeline.briefing_not_found")
            return {"status": "failed", "error": "Briefing not found or inactive"}

        owner_email = await _get_owner_email(session, briefing.user_id)
        if not _emails_match(briefing.email, owner_email):
            await _mark_digest_not_started(
                session,
                digest_id,
                "failed",
                "Delivery email does not match briefing owner",
            )
            log.warning(
                "pipeline.delivery_email_mismatch",
                delivery_email=briefing.email,
                owner_email=owner_email,
            )
            return {
                "status": "failed",
                "error": "Delivery email does not match briefing owner",
            }

        digest = await _get_digest_for_run(session, briefing.id, digest_id)
        if digest is not None and digest.status != "queued":
            log.info(
                "pipeline.digest_already_terminal",
                digest_id=digest.id,
                status=digest.status,
            )
            return {
                "status": digest.status,
                "digest_id": digest.id,
                "reason": "Digest was already processed",
            }

        lock_acquired = await _try_lock_briefing_pipeline(session, briefing.id)
        if not lock_acquired:
            await _mark_digest_not_started(
                session,
                digest_id,
                "skipped",
                "A pipeline run is already active for this briefing",
            )
            log.warning("pipeline.duplicate_run_skipped")
            return {
                "status": "skipped",
                "error": "A pipeline run is already active for this briefing",
            }

        # Build intent context for filters and summariser
        intent_context = build_intent_context(
            topic=briefing.topic,
            intent_description=briefing.intent_description,
            keywords=briefing.keywords,
            example_articles=briefing.example_articles,
            exclusion_keywords=briefing.exclusion_keywords,
        )
        all_keywords = extract_all_keywords(
            keywords=briefing.keywords,
            topic=briefing.topic,
            intent_description=briefing.intent_description,
            example_articles=briefing.example_articles,
        )
        semantic_query = build_semantic_query(
            topic=briefing.topic,
            intent_description=briefing.intent_description,
            keywords=briefing.keywords,
            example_articles=briefing.example_articles,
        )
        last_delivered_at = await _get_last_delivered_at(session, briefing.id)
        source_metadata = await _load_source_metadata(session, briefing.sources)

        # ── Create or claim digest record ─────────────────────────
        if digest is None:
            digest = Digest(briefing_id=briefing.id, status="processing")
            session.add(digest)
            await session.flush()
            await session.refresh(digest)
        else:
            digest.status = "processing"
            await session.flush()
        digest_id = digest.id
        log = log.bind(digest_id=digest_id)

        # ── STAGE 1: FETCH ────────────────────────────────────────
        session.add(PipelineEvent(
            digest_id=digest_id, stage="fetch", status="started",
        ))
        await session.flush()

        try:
            articles, fetch_ms = await fetch_articles(
                sources=briefing.sources,
                topic=briefing.topic,
                source_metadata=source_metadata,
                since=last_delivered_at,
            )

            if not articles:
                if last_delivered_at:
                    session.add(PipelineEvent(
                        digest_id=digest_id, stage="fetch", status="success",
                        duration_ms=fetch_ms,
                        error_msg="No new articles in window",
                        item_count=0,
                    ))
                    session.add(PipelineEvent(
                        digest_id=digest_id, stage="deliver", status="skipped",
                        duration_ms=0,
                        error_msg="No digest delivered because no new articles were fetched",
                        item_count=0,
                    ))
                    digest.status = "skipped"
                    await session.commit()
                    log.info(
                        "pipeline.no_new_articles",
                        duration_ms=fetch_ms,
                        since=last_delivered_at.isoformat(),
                    )
                    return {"status": "skipped", "reason": "No new articles in window"}

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

        # ── STAGE 2: FILTER ───────────────────────────────────────
        session.add(PipelineEvent(
            digest_id=digest_id, stage="filter", status="started",
        ))
        await session.flush()

        filter_start_ms = _now_ms()
        try:
            pipeline = FilterPipeline(
                intent_context=intent_context,
                keywords=all_keywords,
                exclusion_keywords=briefing.exclusion_keywords,
                semantic_query=semantic_query,
            )
            filtered_articles = await pipeline.run(articles)
            filter_ms = _now_ms() - filter_start_ms

            session.add(PipelineEvent(
                digest_id=digest_id, stage="filter", status="success",
                duration_ms=filter_ms,
                item_count=len(filtered_articles),
            ))
            await session.flush()
            log.info(
                "pipeline.filter_done",
                input=len(articles),
                output=len(filtered_articles),
                duration_ms=filter_ms,
                pre_llm_dropped=len(articles) - len(filtered_articles),
            )

            if not filtered_articles:
                session.add(PipelineEvent(
                    digest_id=digest_id, stage="deliver", status="skipped",
                    duration_ms=0,
                    error_msg="No digest delivered because no articles passed relevance filtering",
                    item_count=0,
                ))
                digest.status = "skipped"
                await session.commit()
                log.info("pipeline.no_relevant_articles")
                return {
                    "status": "skipped",
                    "reason": "No articles passed relevance filtering",
                }

        except Exception as exc:
            filter_ms = _now_ms() - filter_start_ms
            await _retry_or_mark_stage_failed(
                session=session,
                digest=digest,
                digest_id=digest_id,
                stage="filter",
                duration_ms=filter_ms,
                exc=exc,
                ctx=ctx,
            )
            log.error("pipeline.filter_error", error=str(exc))
            return {"status": "failed", "error": str(exc), "digest_id": digest_id}

        # ── STAGE 3: SUMMARISE ────────────────────────────────────
        session.add(PipelineEvent(
            digest_id=digest_id, stage="summarise", status="started",
        ))
        await session.flush()

        summarise_start_ms = _now_ms()
        try:
            summarised_articles = await summarise_articles(
                filtered_articles,
                topic=briefing.topic,
                intent_context=intent_context,
            )
            if len(summarised_articles) != len(filtered_articles):
                raise RuntimeError(
                    "Summarisation failed: expected exactly one summary per article"
                )
            summarise_ms = _now_ms() - summarise_start_ms

            session.add(PipelineEvent(
                digest_id=digest_id, stage="summarise", status="success",
                duration_ms=summarise_ms, item_count=len(summarised_articles),
            ))
            await session.flush()
            log.info("pipeline.summarise_done", articles=len(summarised_articles), duration_ms=summarise_ms)

        except Exception as exc:
            summarise_ms = _now_ms() - summarise_start_ms
            await _retry_or_mark_stage_failed(
                session=session,
                digest=digest,
                digest_id=digest_id,
                stage="summarise",
                duration_ms=summarise_ms,
                exc=exc,
                ctx=ctx,
            )
            log.error("pipeline.summarise_error", error=str(exc))
            return {"status": "failed", "error": str(exc), "digest_id": digest_id}

        # ── SAVE DIGEST ITEMS ─────────────────────────────────────
        for article in summarised_articles:
            item = DigestItem(
                digest_id=digest_id,
                source_url=article.get("source_url", ""),
                title=article.get("title", "Untitled"),
                item_url=article.get("url", ""),
                raw_content=article.get("raw_content", ""),
                summary=article.get("summary", "[Summary unavailable]"),
                fetch_duration_ms=fetch_ms,
                published_at=article.get("published_at"),
                updated_at=article.get("updated_at"),
                date_source=article.get("date_source"),
                date_confidence=article.get("date_confidence"),
                date_resolution_status=article.get("date_resolution_status"),
                date_candidates_json=article.get("date_candidates"),
                heuristic_score=article.get("heuristic_score"),
                llm_relevance_score=article.get("llm_relevance_score"),
                llm_relevance_reason=article.get("llm_relevance_reason"),
            )
            session.add(item)
        await session.flush()

        # ── STAGE 4: DELIVER ──────────────────────────────────────
        session.add(PipelineEvent(
            digest_id=digest_id, stage="deliver", status="started",
        ))
        await session.flush()

        try:
            html_body = _build_digest_email(
                briefing.topic, briefing.email, summarised_articles, intent_context,
            )

            start_ms = _now_ms()
            success = await send_digest_email(
                to_email=briefing.email,
                subject=f"SmartDigest: {briefing.topic}",
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
    """ARQ cron job — enqueues pipelines for all active briefings.

    Only enqueues briefings whose delivery email belongs to their owning user
    and whose daily cron schedule is due. If the worker was offline at the exact
    scheduled minute, this catches up the most recent missed daily slot.
    """
    log = logger.bind(job="enqueue_scheduled_digests")
    log.info("cron.checking_briefings")

    settings = get_settings()
    now = ctx.get("now") if isinstance(ctx, dict) else None
    if not isinstance(now, datetime):
        now = datetime.now(timezone.utc)

    async with async_session() as session:
        result = await session.execute(
            select(Briefing)
            .join(User, User.id == Briefing.user_id)
            .where(
                Briefing.active.is_(True),
                func.lower(Briefing.email) == func.lower(User.email),
            )
        )
        briefings = result.scalars().all()

        due_runs = []
        for briefing in briefings:
            scheduled_at = _latest_scheduled_occurrence(briefing.schedule, now)
            if scheduled_at is None:
                continue
            if _is_before_briefing_creation(scheduled_at, briefing):
                continue

            already_queued = await _digest_exists_since(
                session,
                briefing.id,
                scheduled_at,
            )
            if already_queued:
                continue

            digest = Digest(briefing_id=briefing.id, status="queued")
            session.add(digest)
            await session.flush()
            await session.refresh(digest)
            await session.commit()
            due_runs.append((briefing, digest, scheduled_at))

    if not briefings:
        log.info("cron.no_active_briefings")
        return {"enqueued": 0, "queued": 0}

    if not due_runs:
        log.info("cron.no_due_briefings", checked=len(briefings))
        return {"enqueued": 0, "queued": 0}

    redis = await create_pool(RedisSettings.from_dsn(settings.REDIS_URL))
    enqueued = 0
    try:
        for briefing, digest, scheduled_at in due_runs:
            try:
                job = await redis.enqueue_job(
                    "run_pipeline",
                    briefing.id,
                    digest.id,
                    _job_id=f"digest:{digest.id}",
                    _expires=timedelta(seconds=settings.ARQ_JOB_EXPIRES_SECONDS),
                )
                if job is not None:
                    enqueued += 1
                    log.info(
                        "cron.enqueued_digest",
                        briefing_id=briefing.id,
                        digest_id=digest.id,
                        scheduled_at=scheduled_at.isoformat(),
                    )
            except Exception as exc:
                log.error(
                    "cron.enqueue_failed",
                    briefing_id=briefing.id,
                    digest_id=digest.id,
                    error=str(exc),
                )
    finally:
        await redis.close()

    log.info("cron.enqueued", count=enqueued, queued=len(due_runs))
    return {"enqueued": enqueued, "queued": len(due_runs)}


async def recover_queued_digests(ctx: dict) -> dict:
    """Re-enqueue digest runs left behind while the worker was unavailable."""
    settings = get_settings()
    queued_cutoff = datetime.now(timezone.utc) - timedelta(
        minutes=settings.QUEUED_DIGEST_RECOVERY_AFTER_MINUTES
    )
    processing_cutoff = datetime.now(timezone.utc) - timedelta(
        minutes=settings.PROCESSING_DIGEST_RECOVERY_AFTER_MINUTES
    )
    log = logger.bind(
        job="recover_queued_digests",
        queued_cutoff=queued_cutoff.isoformat(),
        processing_cutoff=processing_cutoff.isoformat(),
    )

    async with async_session() as session:
        result = await session.execute(
            select(Digest, Briefing)
            .join(Briefing, Briefing.id == Digest.briefing_id)
            .where(
                Digest.status == "queued",
                Digest.created_at < queued_cutoff,
                Briefing.active.is_(True),
            )
            .order_by(Digest.created_at.asc())
            .limit(50)
        )
        runs = [(digest, briefing, "queued") for digest, briefing in result.all()]

        processing_result = await session.execute(
            select(Digest, Briefing)
            .join(Briefing, Briefing.id == Digest.briefing_id)
            .where(
                Digest.status == "processing",
                Briefing.active.is_(True),
            )
            .order_by(Digest.created_at.asc())
            .limit(50)
        )
        for digest, briefing in processing_result.all():
            latest_event_at = await session.scalar(
                select(func.max(PipelineEvent.created_at)).where(
                    PipelineEvent.digest_id == digest.id,
                )
            )
            stale_reference = latest_event_at or digest.created_at
            if not _is_before_cutoff(stale_reference, processing_cutoff):
                continue

            digest.status = "queued"
            session.add(PipelineEvent(
                digest_id=digest.id,
                stage="queue",
                status="queued",
                error_msg=(
                    "Recovered stale processing digest after worker restart"
                ),
                item_count=0,
            ))
            runs.append((digest, briefing, "processing"))

        if any(original_status == "processing" for _, _, original_status in runs):
            await session.commit()

    if not runs:
        log.info("queue_recovery.none")
        return {"requeued": 0, "recovered_processing": 0}

    redis = await create_pool(RedisSettings.from_dsn(settings.REDIS_URL))
    requeued = 0
    recovered_processing = 0
    try:
        for digest, briefing, original_status in runs:
            try:
                job_id = f"digest:{digest.id}"
                if original_status == "processing":
                    job_id = f"{job_id}:recovery"
                job = await redis.enqueue_job(
                    "run_pipeline",
                    briefing.id,
                    digest.id,
                    _job_id=job_id,
                    _expires=timedelta(seconds=settings.ARQ_JOB_EXPIRES_SECONDS),
                )
                if job is not None:
                    requeued += 1
                    if original_status == "processing":
                        recovered_processing += 1
                    log.info(
                        "queue_recovery.requeued",
                        briefing_id=briefing.id,
                        digest_id=digest.id,
                        original_status=original_status,
                        job_id=job_id,
                    )
            except Exception as exc:
                log.error(
                    "queue_recovery.enqueue_failed",
                    briefing_id=briefing.id,
                    digest_id=digest.id,
                    error=str(exc),
                )
    finally:
        await redis.close()

    return {
        "requeued": requeued,
        "recovered_processing": recovered_processing,
    }


async def _get_owner_email(session, user_id: int) -> str:
    result = await session.execute(
        select(User.email).where(User.id == user_id)
    )
    return result.scalar_one_or_none() or ""


async def _get_last_delivered_at(session, briefing_id: int) -> Optional[datetime]:
    result = await session.execute(
        select(Digest.delivered_at)
        .where(
            Digest.briefing_id == briefing_id,
            Digest.status == "delivered",
            Digest.delivered_at.is_not(None),
        )
        .order_by(Digest.delivered_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _digest_exists_since(
    session,
    briefing_id: int,
    since: datetime,
) -> bool:
    result = await session.execute(
        select(Digest.id)
        .where(
            Digest.briefing_id == briefing_id,
            Digest.created_at >= since,
        )
        .limit(1)
    )
    return result.scalar_one_or_none() is not None


async def _get_digest_for_run(
    session,
    briefing_id: int,
    digest_id: Optional[int],
) -> Optional[Digest]:
    if digest_id is None:
        return None

    result = await session.execute(
        select(Digest).where(
            Digest.id == digest_id,
            Digest.briefing_id == briefing_id,
        )
    )
    return result.scalar_one_or_none()


async def _mark_digest_not_started(
    session,
    digest_id: Optional[int],
    status: str,
    reason: str,
) -> None:
    if digest_id is None:
        return

    result = await session.execute(select(Digest).where(Digest.id == digest_id))
    digest = result.scalar_one_or_none()
    if digest is None or digest.status != "queued":
        return

    digest.status = status
    session.add(PipelineEvent(
        digest_id=digest.id,
        stage="queue",
        status=status,
        error_msg=reason,
        item_count=0,
    ))
    await session.commit()


async def _retry_or_mark_stage_failed(
    session,
    digest: Digest,
    digest_id: int,
    stage: str,
    duration_ms: int,
    exc: Exception,
    ctx: dict,
) -> None:
    settings = get_settings()
    job_try = int((ctx or {}).get("job_try") or 1)
    max_tries = max(1, int(getattr(settings, "ARQ_MAX_TRIES", 1)))

    if isinstance(exc, LLMRetryableError) and job_try < max_tries:
        defer_seconds = max(
            1,
            int(getattr(settings, "PIPELINE_RETRY_DEFER_SECONDS", 300)),
        )
        digest.status = "queued"
        session.add(PipelineEvent(
            digest_id=digest_id,
            stage=stage,
            status="retrying",
            duration_ms=duration_ms,
            error_msg=f"{exc} (attempt {job_try}/{max_tries})",
        ))
        await session.commit()
        logger.warning(
            "pipeline.stage_retrying",
            digest_id=digest_id,
            stage=stage,
            attempt=job_try,
            max_tries=max_tries,
            defer_seconds=defer_seconds,
            error=str(exc),
        )
        raise Retry(defer=defer_seconds)

    session.add(PipelineEvent(
        digest_id=digest_id,
        stage=stage,
        status="failed",
        duration_ms=duration_ms,
        error_msg=str(exc),
    ))
    digest.status = "failed"
    await session.commit()


def _emails_match(left: Optional[str], right: Optional[str]) -> bool:
    return (left or "").strip().lower() == (right or "").strip().lower()


def _is_before_cutoff(value: Optional[datetime], cutoff: datetime) -> bool:
    if value is None:
        return False
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    return value < cutoff


async def _try_lock_briefing_pipeline(session, briefing_id: int) -> bool:
    result = await session.execute(
        select(func.pg_try_advisory_xact_lock(_PIPELINE_LOCK_NAMESPACE, briefing_id))
    )
    return bool(result.scalar())


def _schedule_is_due(schedule: Optional[str], now: datetime) -> bool:
    """Return true when a simple daily cron is due at the current UTC minute."""
    if not schedule:
        return False

    parts = schedule.strip().split()
    if len(parts) != 5 or parts[2:] != ["*", "*", "*"]:
        return False

    try:
        minute = int(parts[0])
        hour = int(parts[1])
    except ValueError:
        return False

    return now.hour == hour and now.minute == minute


def _latest_scheduled_occurrence(
    schedule: Optional[str],
    now: datetime,
) -> Optional[datetime]:
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    else:
        now = now.astimezone(timezone.utc)

    parts = (schedule or "").strip().split()
    if len(parts) != 5 or parts[2:] != ["*", "*", "*"]:
        return None

    try:
        minute = int(parts[0])
        hour = int(parts[1])
    except ValueError:
        return None

    if hour < 0 or hour > 23 or minute not in {0, 30}:
        return None

    scheduled = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if scheduled > now:
        scheduled -= timedelta(days=1)
    return scheduled


def _is_before_briefing_creation(scheduled_at: datetime, briefing: Briefing) -> bool:
    created_at = getattr(briefing, "created_at", None)
    if created_at is None:
        return False
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    else:
        created_at = created_at.astimezone(timezone.utc)
    return scheduled_at < created_at


def _build_digest_email(
    topic: str,
    email: str,
    articles: list,
    intent_context: str = "",
) -> str:
    """Build a beautiful HTML email with intent-aware article summaries."""
    items_html = ""
    for article in articles:
        title = article.get("title", "Untitled")
        url = article.get("url", "#")
        summary = article.get("summary", "[Summary unavailable]")
        source = article.get("source_url", "").replace("https://", "").replace("http://", "")
        if len(source) > 40:
            source = source[:40] + "..."

        # Show relevance score badge if available
        relevance_badge = ""
        llm_score = article.get("llm_relevance_score")
        if llm_score:
            if llm_score >= 8:
                badge_color = "#059669"
                badge_bg = "#d1fae5"
                badge_text = f"⭐ {llm_score}/10"
            elif llm_score >= 6:
                badge_color = "#2563eb"
                badge_bg = "#dbeafe"
                badge_text = f"✓ {llm_score}/10"
            else:
                badge_color = "#6b7280"
                badge_bg = "#f3f4f6"
                badge_text = f"{llm_score}/10"
            relevance_badge = (
                f'<span style="background:{badge_bg}; color:{badge_color}; '
                f'font-size:11px; padding:2px 6px; border-radius:4px; '
                f'margin-left:8px;">{badge_text}</span>'
            )

        items_html += f"""
        <div style="margin-bottom: 20px; padding: 16px; background: #f9fafb; border-radius: 8px; border: 1px solid #e5e7eb;">
            <a href="{url}" style="color: #2563eb; text-decoration: none; font-weight: 600; font-size: 15px;">{title}</a>
            {relevance_badge}
            <div style="color: #9ca3af; font-size: 12px; margin: 4px 0 8px;">{source}</div>
            <p style="color: #374151; font-size: 14px; line-height: 1.5; margin: 0;">{summary}</p>
        </div>
        """

    return f"""
    <html>
    <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px; background: #ffffff;">
        <div style="text-align: center; margin-bottom: 24px;">
            <h1 style="color: #1f2937; margin: 0;">📰 SmartDigest</h1>
            <p style="color: #6b7280; font-size: 14px;">Your AI-curated briefing for <strong>{topic}</strong></p>
        </div>

        <div style="background: #eff6ff; border-radius: 8px; padding: 12px 16px; margin-bottom: 24px; text-align: center;">
            <span style="color: #1d4ed8; font-size: 14px;">📊 {len(articles)} relevant articles found and summarised</span>
        </div>

        {items_html}

        <hr style="border: none; border-top: 1px solid #e5e7eb; margin: 24px 0;">
        <p style="color: #9ca3af; font-size: 12px; text-align: center;">
            Sent by SmartDigest to {email}<br>
            AI-filtered and summarised for your specific interests
        </p>
    </body>
    </html>
    """


def _now_ms() -> int:
    """Current time in milliseconds."""
    return int(datetime.now(timezone.utc).timestamp() * 1000)

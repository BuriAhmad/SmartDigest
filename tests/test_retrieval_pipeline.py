import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from app.api.briefings import trigger_pipeline
from app.models.digest import Digest
from app.models.digest_item import DigestItem
from app.models.pipeline_event import PipelineEvent
from app.services.publication_dates import STATUS_UPDATED_ONLY, resolve_publication_date
from app.services.fetcher import fetch_articles
from app.services.filters import FilterPipeline
from app.services.filters.bm25 import BM25Filter
from app.services.filters.llm_relevance import LLMRelevanceFilter, RelevanceScore
from app.services.filters.semantic import SemanticRetriever
from app.services.intent import extract_all_keywords
from app.services.llm import configured_models
from app.services.scrapers import build_default_registry
from app.services.scrapers.hackernews import HackerNewsScraper
from app.services.scrapers.rss_generic import GenericRSSScraper
from app.services.summariser import SummaryResult
from app.services.scheduler import (
    _latest_scheduled_occurrence,
    enqueue_scheduled_digests,
    recover_queued_digests,
    run_pipeline,
)
from app.services.summariser import summarise_articles


class FakeRawArticle:
    def __init__(self, index, published_at):
        self.index = index
        self.published_at = published_at

    def to_dict(self):
        return {
            "title": f"Article {self.index}",
            "url": f"https://example.com/{self.index}",
            "source_url": "https://example.com/rss",
            "raw_content": f"Body {self.index}",
            "published_at": self.published_at,
            "tags": [],
        }


class FakeScraper:
    seen_since = None
    articles = []

    async def fetch_articles(self, source_url, source_name="", scraper_config=None, since=None):
        FakeScraper.seen_since = since
        return FakeScraper.articles


class FakeRegistry:
    def get_scraper(self, name, url):
        return FakeScraper()


class FakeResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value

    def scalar(self):
        return self.value

    def all(self):
        return self.value

    def scalars(self):
        return self


class FakeSession:
    def __init__(self, briefing, execute_values=None):
        self.briefing = briefing
        self.execute_values = list(execute_values) if execute_values is not None else None
        self.added = []
        self.committed = False
        self.flushed = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute(self, _query):
        if self.execute_values is not None:
            return FakeResult(self.execute_values.pop(0))
        return FakeResult(self.briefing)

    def add(self, item):
        self.added.append(item)

    async def flush(self):
        self.flushed += 1

    async def refresh(self, item):
        if isinstance(item, Digest):
            item.id = 101

    async def commit(self):
        self.committed = True


class FakeScheduleSession(FakeSession):
    async def refresh(self, item):
        if isinstance(item, Digest):
            item.id = 505


class FakeFilterPipeline:
    seen_articles = None

    def __init__(self, intent_context, keywords, exclusion_keywords, semantic_query=None):
        self.intent_context = intent_context
        self.keywords = keywords
        self.exclusion_keywords = exclusion_keywords
        self.semantic_query = semantic_query

    async def run(self, articles):
        FakeFilterPipeline.seen_articles = articles
        article = dict(articles[0])
        article["heuristic_score"] = 0.8
        article["llm_relevance_score"] = 8
        article["llm_relevance_reason"] = "Relevant"
        return [article]


class EmptyFilterPipeline:
    def __init__(self, intent_context, keywords, exclusion_keywords, semantic_query=None):
        self.intent_context = intent_context
        self.keywords = keywords
        self.exclusion_keywords = exclusion_keywords
        self.semantic_query = semantic_query

    async def run(self, articles):
        return []


class FailingFilterPipeline:
    def __init__(self, intent_context, keywords, exclusion_keywords, semantic_query=None):
        self.intent_context = intent_context
        self.keywords = keywords
        self.exclusion_keywords = exclusion_keywords
        self.semantic_query = semantic_query

    async def run(self, articles):
        raise RuntimeError("LLM relevance scoring failed: HTTP 503 on gemini-2.5-flash-lite")


class FakeLLMFilter:
    def __init__(self):
        self.seen_articles = None

    async def score_and_filter(self, articles):
        self.seen_articles = articles
        return articles


class StaticSemanticFilter:
    def __init__(self, articles):
        self.articles = articles

    async def retrieve(self, _articles):
        return self.articles


class FakeRedis:
    def __init__(self, job=object()):
        self.job = job
        self.enqueue_args = None
        self.enqueue_kwargs = None
        self.enqueue_calls = []
        self.closed = False

    async def enqueue_job(self, *args, **kwargs):
        self.enqueue_args = args
        self.enqueue_kwargs = kwargs
        self.enqueue_calls.append((args, kwargs))
        return self.job

    async def close(self):
        self.closed = True


class FakeEmbeddingModel:
    def encode(self, texts, normalize_embeddings=True, show_progress_bar=False):
        vectors = []
        for text in texts:
            if "governance" in text.lower() or "oversight" in text.lower():
                vectors.append([1.0, 0.0])
            else:
                vectors.append([0.0, 1.0])
        return vectors


def make_briefing():
    return SimpleNamespace(
        id=7,
        user_id=3,
        active=True,
        topic="AI policy",
        intent_description="Track model governance and safety regulation",
        keywords=["AI safety"],
        example_articles=[],
        exclusion_keywords=["crypto"],
        sources=["https://example.com/rss"],
        email="user@example.com",
        schedule="0 7 * * *",
        created_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
    )


class RetrievalPipelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_manual_trigger_creates_queued_digest_and_enqueues_that_digest(self):
        briefing = make_briefing()
        fake_session = FakeSession(briefing)
        fake_redis = FakeRedis()
        request = SimpleNamespace(
            state=SimpleNamespace(user_id=briefing.user_id, user_email=briefing.email)
        )
        trigger = trigger_pipeline.__wrapped__

        with patch(
            "app.api.briefings.get_settings",
            return_value=SimpleNamespace(
                REDIS_URL="redis://test",
                ARQ_JOB_EXPIRES_SECONDS=604800,
            ),
        ), \
             patch("app.api.briefings.RedisSettings.from_dsn", return_value="redis-settings"), \
             patch("app.api.briefings.create_pool", AsyncMock(return_value=fake_redis)):
            result = await trigger(briefing.id, request, fake_session)

        digests = [item for item in fake_session.added if isinstance(item, Digest)]
        self.assertEqual(digests[-1].status, "queued")
        self.assertEqual(result["status"], "queued")
        self.assertEqual(result["digest_id"], 101)
        self.assertEqual(result["job_id"], "digest:101")
        self.assertEqual(fake_redis.enqueue_args, ("run_pipeline", briefing.id, 101))
        self.assertEqual(fake_redis.enqueue_kwargs["_job_id"], "digest:101")
        self.assertIn("_expires", fake_redis.enqueue_kwargs)
        self.assertTrue(fake_redis.closed)

    async def test_manual_trigger_rejects_when_arq_does_not_accept_job(self):
        briefing = make_briefing()
        fake_session = FakeSession(briefing)
        fake_redis = FakeRedis(job=None)
        request = SimpleNamespace(
            state=SimpleNamespace(user_id=briefing.user_id, user_email=briefing.email)
        )
        trigger = trigger_pipeline.__wrapped__

        with patch(
            "app.api.briefings.get_settings",
            return_value=SimpleNamespace(
                REDIS_URL="redis://test",
                ARQ_JOB_EXPIRES_SECONDS=604800,
            ),
        ), \
             patch("app.api.briefings.RedisSettings.from_dsn", return_value="redis-settings"), \
             patch("app.api.briefings.create_pool", AsyncMock(return_value=fake_redis)), \
             self.assertRaises(HTTPException) as raised:
            await trigger(briefing.id, request, fake_session)

        self.assertEqual(raised.exception.status_code, 503)
        self.assertTrue(fake_redis.closed)

    async def test_recovery_requeues_stale_queued_digests_with_stable_job_id(self):
        briefing = make_briefing()
        queued_digest = Digest(briefing_id=briefing.id, status="queued")
        queued_digest.id = 303
        fake_session = FakeSession(
            briefing,
            execute_values=[[(queued_digest, briefing)]],
        )
        fake_redis = FakeRedis()

        with patch("app.services.scheduler.async_session", return_value=fake_session), \
             patch(
                 "app.services.scheduler.get_settings",
                 return_value=SimpleNamespace(
                     REDIS_URL="redis://test",
                     QUEUED_DIGEST_RECOVERY_AFTER_MINUTES=5,
                     ARQ_JOB_EXPIRES_SECONDS=604800,
                 ),
             ), \
             patch("app.services.scheduler.RedisSettings.from_dsn", return_value="redis-settings"), \
             patch("app.services.scheduler.create_pool", AsyncMock(return_value=fake_redis)):
            result = await recover_queued_digests({})

        self.assertEqual(result["requeued"], 1)
        self.assertEqual(fake_redis.enqueue_args, ("run_pipeline", briefing.id, queued_digest.id))
        self.assertEqual(fake_redis.enqueue_kwargs["_job_id"], "digest:303")
        self.assertIn("_expires", fake_redis.enqueue_kwargs)
        self.assertTrue(fake_redis.closed)

    async def test_scheduled_enqueue_catches_up_missed_daily_slot_with_queued_digest(self):
        briefing = make_briefing()
        briefing.schedule = "0 7 * * *"
        fake_session = FakeScheduleSession(
            briefing,
            execute_values=[[briefing], None],
        )
        fake_redis = FakeRedis()
        now = datetime(2026, 6, 23, 12, 15, tzinfo=timezone.utc)

        with patch("app.services.scheduler.async_session", return_value=fake_session), \
             patch(
                 "app.services.scheduler.get_settings",
                 return_value=SimpleNamespace(
                     REDIS_URL="redis://test",
                     ARQ_JOB_EXPIRES_SECONDS=604800,
                 ),
             ), \
             patch("app.services.scheduler.RedisSettings.from_dsn", return_value="redis-settings"), \
             patch("app.services.scheduler.create_pool", AsyncMock(return_value=fake_redis)):
            result = await enqueue_scheduled_digests({"now": now})

        digests = [item for item in fake_session.added if isinstance(item, Digest)]
        self.assertEqual(result, {"enqueued": 1, "queued": 1})
        self.assertEqual(digests[-1].status, "queued")
        self.assertTrue(fake_session.committed)
        self.assertEqual(fake_redis.enqueue_args, ("run_pipeline", briefing.id, 505))
        self.assertEqual(fake_redis.enqueue_kwargs["_job_id"], "digest:505")
        self.assertIn("_expires", fake_redis.enqueue_kwargs)
        self.assertTrue(fake_redis.closed)

    async def test_scheduled_enqueue_does_not_duplicate_existing_slot_digest(self):
        briefing = make_briefing()
        briefing.schedule = "0 7 * * *"
        fake_session = FakeScheduleSession(
            briefing,
            execute_values=[[briefing], 999],
        )
        now = datetime(2026, 6, 23, 12, 15, tzinfo=timezone.utc)

        with patch("app.services.scheduler.async_session", return_value=fake_session):
            result = await enqueue_scheduled_digests({"now": now})

        self.assertEqual(result, {"enqueued": 0, "queued": 0})
        self.assertEqual([item for item in fake_session.added if isinstance(item, Digest)], [])

    async def test_scheduled_enqueue_does_not_backfill_before_briefing_existed(self):
        briefing = make_briefing()
        briefing.schedule = "30 7 * * *"
        briefing.created_at = datetime(2026, 6, 23, 7, 0, tzinfo=timezone.utc)
        fake_session = FakeScheduleSession(
            briefing,
            execute_values=[[briefing]],
        )
        now = datetime(2026, 6, 23, 7, 10, tzinfo=timezone.utc)

        with patch("app.services.scheduler.async_session", return_value=fake_session):
            result = await enqueue_scheduled_digests({"now": now})

        self.assertEqual(result, {"enqueued": 0, "queued": 0})
        self.assertEqual([item for item in fake_session.added if isinstance(item, Digest)], [])

    def test_latest_scheduled_occurrence_catches_up_today_or_yesterday(self):
        today = datetime(2026, 6, 23, 12, 15, tzinfo=timezone.utc)
        before_slot = datetime(2026, 6, 23, 6, 45, tzinfo=timezone.utc)

        self.assertEqual(
            _latest_scheduled_occurrence("0 7 * * *", today),
            datetime(2026, 6, 23, 7, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(
            _latest_scheduled_occurrence("0 7 * * *", before_slot),
            datetime(2026, 6, 22, 7, 0, tzinfo=timezone.utc),
        )
        self.assertIsNone(_latest_scheduled_occurrence("15 7 * * *", today))

    async def test_fetcher_passes_since_and_does_not_apply_global_cap(self):
        since = datetime(2026, 6, 11, tzinfo=timezone.utc)
        FakeScraper.articles = [
            FakeRawArticle(index, since + timedelta(minutes=index + 1))
            for index in range(25)
        ]

        with patch("app.services.fetcher.build_default_registry", return_value=FakeRegistry()):
            articles, _duration_ms = await fetch_articles(
                sources=["https://example.com/rss"],
                topic="AI",
                since=since,
            )

        self.assertEqual(FakeScraper.seen_since, since)
        self.assertEqual(len(articles), 25)

    async def test_fetcher_excludes_old_equal_and_undated_articles_when_since_is_set(self):
        since = datetime(2026, 6, 11, tzinfo=timezone.utc)
        FakeScraper.articles = [
            FakeRawArticle(1, since - timedelta(seconds=1)),
            FakeRawArticle(2, since),
            FakeRawArticle(3, None),
            FakeRawArticle(4, since + timedelta(seconds=1)),
        ]

        with patch("app.services.fetcher.build_default_registry", return_value=FakeRegistry()):
            articles, _duration_ms = await fetch_articles(
                sources=["https://example.com/rss"],
                topic="AI",
                since=since,
            )

        self.assertEqual([article["title"] for article in articles], ["Article 4"])

    async def test_scheduler_skips_empty_window_without_marking_failure(self):
        briefing = make_briefing()
        fake_session = FakeSession(briefing)
        since = datetime(2026, 6, 11, tzinfo=timezone.utc)

        with patch("app.services.scheduler.async_session", return_value=fake_session), \
             patch("app.services.scheduler._get_owner_email", AsyncMock(return_value=briefing.email)), \
             patch("app.services.scheduler._try_lock_briefing_pipeline", AsyncMock(return_value=True)), \
             patch("app.services.scheduler._get_last_delivered_at", AsyncMock(return_value=since)), \
             patch("app.services.scheduler.fetch_articles", AsyncMock(return_value=([], 12))) as fetch_mock, \
             patch("app.services.scheduler.FilterPipeline") as filter_mock, \
             patch("app.services.scheduler.summarise_articles", AsyncMock()) as summarise_mock, \
             patch("app.services.scheduler.send_digest_email", AsyncMock()) as mail_mock:
            result = await run_pipeline({}, briefing.id)

        fetch_mock.assert_awaited_once_with(
            sources=briefing.sources,
            topic=briefing.topic,
            since=since,
        )
        self.assertEqual(result["status"], "skipped")
        self.assertTrue(fake_session.committed)
        self.assertFalse(filter_mock.called)
        self.assertFalse(summarise_mock.called)
        self.assertFalse(mail_mock.called)

        digests = [item for item in fake_session.added if isinstance(item, Digest)]
        events = [item for item in fake_session.added if isinstance(item, PipelineEvent)]
        self.assertEqual(digests[-1].status, "skipped")
        self.assertIn(("fetch", "success", 0), [(e.stage, e.status, e.item_count) for e in events])
        self.assertIn(("deliver", "skipped", 0), [(e.stage, e.status, e.item_count) for e in events])

    async def test_scheduler_uses_last_delivered_timestamp_and_flows_into_filter(self):
        briefing = make_briefing()
        fake_session = FakeSession(briefing)
        since = datetime(2026, 6, 11, tzinfo=timezone.utc)
        article = {
            "title": "AI safety regulation advances",
            "url": "https://example.com/a",
            "source_url": "https://example.com/rss",
            "raw_content": "New model governance rules were published.",
            "published_at": since + timedelta(hours=1),
            "updated_at": since + timedelta(hours=2),
            "date_source": "json_ld_date_published",
            "date_confidence": "high",
            "date_resolution_status": "resolved",
            "date_candidates": [
                {
                    "value": (since + timedelta(hours=1)).isoformat(),
                    "source": "json_ld_date_published",
                    "confidence": "high",
                    "kind": "published",
                }
            ],
            "tags": ["AI"],
        }

        with patch("app.services.scheduler.async_session", return_value=fake_session), \
             patch("app.services.scheduler._get_owner_email", AsyncMock(return_value=briefing.email)), \
             patch("app.services.scheduler._try_lock_briefing_pipeline", AsyncMock(return_value=True)), \
             patch("app.services.scheduler._get_last_delivered_at", AsyncMock(return_value=since)), \
             patch("app.services.scheduler.fetch_articles", AsyncMock(return_value=([article], 10))) as fetch_mock, \
             patch("app.services.scheduler.FilterPipeline", FakeFilterPipeline), \
             patch("app.services.scheduler.summarise_articles", AsyncMock(side_effect=lambda articles, **_: articles)), \
             patch("app.services.scheduler.send_digest_email", AsyncMock(return_value=True)):
            result = await run_pipeline({}, briefing.id)

        fetch_mock.assert_awaited_once_with(
            sources=briefing.sources,
            topic=briefing.topic,
            since=since,
        )
        self.assertEqual(result["status"], "delivered")
        self.assertEqual(FakeFilterPipeline.seen_articles, [article])
        digest_items = [item for item in fake_session.added if isinstance(item, DigestItem)]
        self.assertEqual(digest_items[-1].date_source, "json_ld_date_published")
        self.assertEqual(digest_items[-1].date_resolution_status, "resolved")

    async def test_scheduler_claims_existing_queued_digest_for_manual_run(self):
        briefing = make_briefing()
        queued_digest = Digest(briefing_id=briefing.id, status="queued")
        queued_digest.id = 202
        fake_session = FakeSession(
            briefing,
            execute_values=[briefing, queued_digest, queued_digest],
        )
        article = {
            "title": "AI safety regulation advances",
            "url": "https://example.com/a",
            "source_url": "https://example.com/rss",
            "raw_content": "New model governance rules were published.",
            "published_at": datetime(2026, 6, 11, tzinfo=timezone.utc),
            "tags": ["AI"],
        }

        with patch("app.services.scheduler.async_session", return_value=fake_session), \
             patch("app.services.scheduler._get_owner_email", AsyncMock(return_value=briefing.email)), \
             patch("app.services.scheduler._try_lock_briefing_pipeline", AsyncMock(return_value=True)), \
             patch("app.services.scheduler._get_last_delivered_at", AsyncMock(return_value=None)), \
             patch("app.services.scheduler.fetch_articles", AsyncMock(return_value=([article], 10))), \
             patch("app.services.scheduler.FilterPipeline", FakeFilterPipeline), \
             patch("app.services.scheduler.summarise_articles", AsyncMock(side_effect=lambda articles, **_: articles)), \
             patch("app.services.scheduler.send_digest_email", AsyncMock(return_value=True)):
            result = await run_pipeline({}, briefing.id, queued_digest.id)

        created_digests = [item for item in fake_session.added if isinstance(item, Digest)]
        self.assertEqual(created_digests, [])
        self.assertEqual(result["digest_id"], queued_digest.id)
        self.assertEqual(queued_digest.status, "delivered")
        self.assertTrue(fake_session.committed)

    async def test_scheduler_marks_queued_digest_skipped_when_duplicate_lock_blocks_it(self):
        briefing = make_briefing()
        queued_digest = Digest(briefing_id=briefing.id, status="queued")
        queued_digest.id = 404
        fake_session = FakeSession(
            briefing,
            execute_values=[briefing, queued_digest, queued_digest],
        )

        with patch("app.services.scheduler.async_session", return_value=fake_session), \
             patch("app.services.scheduler._get_owner_email", AsyncMock(return_value=briefing.email)), \
             patch("app.services.scheduler._try_lock_briefing_pipeline", AsyncMock(return_value=False)), \
             patch("app.services.scheduler.fetch_articles", AsyncMock()) as fetch_mock:
            result = await run_pipeline({}, briefing.id, queued_digest.id)

        self.assertEqual(result["status"], "skipped")
        self.assertEqual(queued_digest.status, "skipped")
        self.assertTrue(fake_session.committed)
        self.assertFalse(fetch_mock.called)
        events = [item for item in fake_session.added if isinstance(item, PipelineEvent)]
        self.assertIn(("queue", "skipped"), [(event.stage, event.status) for event in events])

    async def test_scheduler_ignores_stale_redis_job_for_terminal_digest(self):
        briefing = make_briefing()
        skipped_digest = Digest(briefing_id=briefing.id, status="skipped")
        skipped_digest.id = 606
        fake_session = FakeSession(
            briefing,
            execute_values=[briefing, skipped_digest],
        )

        with patch("app.services.scheduler.async_session", return_value=fake_session), \
             patch("app.services.scheduler._get_owner_email", AsyncMock(return_value=briefing.email)), \
             patch("app.services.scheduler._try_lock_briefing_pipeline", AsyncMock()) as lock_mock, \
             patch("app.services.scheduler.fetch_articles", AsyncMock()) as fetch_mock:
            result = await run_pipeline({}, briefing.id, skipped_digest.id)

        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "Digest was already processed")
        self.assertFalse(lock_mock.called)
        self.assertFalse(fetch_mock.called)
        self.assertEqual(skipped_digest.status, "skipped")

    async def test_scheduler_fails_when_no_articles_pass_filtering(self):
        briefing = make_briefing()
        fake_session = FakeSession(briefing)
        article = {
            "title": "Unrelated finance update",
            "url": "https://example.com/finance",
            "source_url": "https://example.com/rss",
            "raw_content": "Quarterly earnings and market moves.",
            "published_at": datetime(2026, 6, 11, tzinfo=timezone.utc),
            "tags": ["finance"],
        }

        with patch("app.services.scheduler.async_session", return_value=fake_session), \
             patch("app.services.scheduler._get_owner_email", AsyncMock(return_value=briefing.email)), \
             patch("app.services.scheduler._try_lock_briefing_pipeline", AsyncMock(return_value=True)), \
             patch("app.services.scheduler._get_last_delivered_at", AsyncMock(return_value=None)), \
             patch("app.services.scheduler.fetch_articles", AsyncMock(return_value=([article], 10))), \
             patch("app.services.scheduler.FilterPipeline", EmptyFilterPipeline), \
             patch("app.services.scheduler.summarise_articles", AsyncMock()) as summarise_mock, \
             patch("app.services.scheduler.send_digest_email", AsyncMock()) as mail_mock:
            result = await run_pipeline({}, briefing.id)

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"], "No relevant articles found")
        self.assertFalse(summarise_mock.called)
        self.assertFalse(mail_mock.called)
        events = [item for item in fake_session.added if isinstance(item, PipelineEvent)]
        self.assertIn(("filter", "failed"), [(event.stage, event.status) for event in events])

    async def test_scheduler_fails_before_email_when_llm_relevance_scoring_fails(self):
        briefing = make_briefing()
        fake_session = FakeSession(briefing)
        article = {
            "title": "AI safety regulation advances",
            "url": "https://example.com/a",
            "source_url": "https://example.com/rss",
            "raw_content": "New model governance rules were published.",
            "published_at": datetime(2026, 6, 11, tzinfo=timezone.utc),
            "tags": ["AI"],
        }

        with patch("app.services.scheduler.async_session", return_value=fake_session), \
             patch("app.services.scheduler._get_owner_email", AsyncMock(return_value=briefing.email)), \
             patch("app.services.scheduler._try_lock_briefing_pipeline", AsyncMock(return_value=True)), \
             patch("app.services.scheduler._get_last_delivered_at", AsyncMock(return_value=None)), \
             patch("app.services.scheduler.fetch_articles", AsyncMock(return_value=([article], 10))), \
             patch("app.services.scheduler.FilterPipeline", FailingFilterPipeline), \
             patch("app.services.scheduler.summarise_articles", AsyncMock()) as summarise_mock, \
             patch("app.services.scheduler.send_digest_email", AsyncMock()) as mail_mock:
            result = await run_pipeline({}, briefing.id)

        self.assertEqual(result["status"], "failed")
        self.assertIn("LLM relevance scoring failed", result["error"])
        self.assertFalse(summarise_mock.called)
        self.assertFalse(mail_mock.called)
        digest_items = [item for item in fake_session.added if isinstance(item, DigestItem)]
        self.assertEqual(digest_items, [])
        events = [item for item in fake_session.added if isinstance(item, PipelineEvent)]
        failed_events = [
            event for event in events
            if event.stage == "filter" and event.status == "failed"
        ]
        self.assertTrue(failed_events)
        self.assertIn("LLM relevance scoring failed", failed_events[-1].error_msg)

    async def test_scheduler_fails_without_email_when_summariser_fails(self):
        briefing = make_briefing()
        fake_session = FakeSession(briefing)
        article = {
            "title": "AI safety regulation advances",
            "url": "https://example.com/a",
            "source_url": "https://example.com/rss",
            "raw_content": "New model governance rules were published.",
            "published_at": datetime(2026, 6, 11, tzinfo=timezone.utc),
            "tags": ["AI"],
        }

        with patch("app.services.scheduler.async_session", return_value=fake_session), \
             patch("app.services.scheduler._get_owner_email", AsyncMock(return_value=briefing.email)), \
             patch("app.services.scheduler._try_lock_briefing_pipeline", AsyncMock(return_value=True)), \
             patch("app.services.scheduler._get_last_delivered_at", AsyncMock(return_value=None)), \
             patch("app.services.scheduler.fetch_articles", AsyncMock(return_value=([article], 10))), \
             patch("app.services.scheduler.FilterPipeline", FakeFilterPipeline), \
             patch("app.services.scheduler.summarise_articles", AsyncMock(side_effect=RuntimeError("LLM down"))), \
             patch("app.services.scheduler.send_digest_email", AsyncMock()) as mail_mock:
            result = await run_pipeline({}, briefing.id)

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"], "LLM down")
        self.assertFalse(mail_mock.called)
        digest_items = [item for item in fake_session.added if isinstance(item, DigestItem)]
        self.assertEqual(digest_items, [])
        events = [item for item in fake_session.added if isinstance(item, PipelineEvent)]
        self.assertIn(("summarise", "failed"), [(event.stage, event.status) for event in events])

    async def test_scheduler_skips_delivery_when_summariser_excludes_every_article(self):
        briefing = make_briefing()
        fake_session = FakeSession(briefing)
        article = {
            "title": "AI safety regulation advances",
            "url": "https://example.com/a",
            "source_url": "https://example.com/rss",
            "raw_content": "New model governance rules were published.",
            "published_at": datetime(2026, 6, 11, tzinfo=timezone.utc),
            "tags": ["AI"],
        }

        with patch("app.services.scheduler.async_session", return_value=fake_session), \
             patch("app.services.scheduler._get_owner_email", AsyncMock(return_value=briefing.email)), \
             patch("app.services.scheduler._try_lock_briefing_pipeline", AsyncMock(return_value=True)), \
             patch("app.services.scheduler._get_last_delivered_at", AsyncMock(return_value=None)), \
             patch("app.services.scheduler.fetch_articles", AsyncMock(return_value=([article], 10))), \
             patch("app.services.scheduler.FilterPipeline", FakeFilterPipeline), \
             patch("app.services.scheduler.summarise_articles", AsyncMock(return_value=[])), \
             patch("app.services.scheduler.send_digest_email", AsyncMock()) as mail_mock:
            result = await run_pipeline({}, briefing.id)

        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "No articles remained after summarisation")
        self.assertFalse(mail_mock.called)
        digest_items = [item for item in fake_session.added if isinstance(item, DigestItem)]
        self.assertEqual(digest_items, [])
        events = [item for item in fake_session.added if isinstance(item, PipelineEvent)]
        self.assertIn(("deliver", "skipped"), [(event.stage, event.status) for event in events])

    async def test_scheduler_marks_digest_failed_when_email_delivery_fails(self):
        briefing = make_briefing()
        fake_session = FakeSession(briefing)
        article = {
            "title": "AI safety regulation advances",
            "url": "https://example.com/a",
            "source_url": "https://example.com/rss",
            "raw_content": "New model governance rules were published.",
            "published_at": datetime(2026, 6, 11, tzinfo=timezone.utc),
            "tags": ["AI"],
        }

        with patch("app.services.scheduler.async_session", return_value=fake_session), \
             patch("app.services.scheduler._get_owner_email", AsyncMock(return_value=briefing.email)), \
             patch("app.services.scheduler._try_lock_briefing_pipeline", AsyncMock(return_value=True)), \
             patch("app.services.scheduler._get_last_delivered_at", AsyncMock(return_value=None)), \
             patch("app.services.scheduler.fetch_articles", AsyncMock(return_value=([article], 10))), \
             patch("app.services.scheduler.FilterPipeline", FakeFilterPipeline), \
             patch("app.services.scheduler.summarise_articles", AsyncMock(side_effect=lambda articles, **_: articles)), \
             patch("app.services.scheduler.send_digest_email", AsyncMock(return_value=False)):
            result = await run_pipeline({}, briefing.id)

        self.assertEqual(result["status"], "failed")
        digest_items = [item for item in fake_session.added if isinstance(item, DigestItem)]
        self.assertEqual(len(digest_items), 1)
        events = [item for item in fake_session.added if isinstance(item, PipelineEvent)]
        self.assertIn(("deliver", "failed"), [(event.stage, event.status) for event in events])


class PublicationDateResolverTests(unittest.TestCase):
    def test_json_ld_date_published_recovers_page_publication_date(self):
        html = """
        <html><head>
          <script type="application/ld+json">
            {"@type":"NewsArticle","datePublished":"2026-06-12T09:30:00Z"}
          </script>
        </head></html>
        """

        result = resolve_publication_date(html=html, url="https://example.com/a")

        self.assertEqual(result.status, "resolved")
        self.assertEqual(result.source, "json_ld_date_published")
        self.assertEqual(result.confidence, "high")
        self.assertEqual(result.published_at, datetime(2026, 6, 12, 9, 30, tzinfo=timezone.utc))

    def test_feed_updated_is_not_treated_as_published_date(self):
        entry = SimpleNamespace(
            updated_parsed=(2026, 6, 12, 9, 30, 0, 0, 0, 0),
        )

        result = resolve_publication_date(feed_entry=entry)

        self.assertIsNone(result.published_at)
        self.assertEqual(result.updated_at, datetime(2026, 6, 12, 9, 30, tzinfo=timezone.utc))
        self.assertEqual(result.status, STATUS_UPDATED_ONLY)


class GenericRSSScraperDateTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_feed_date_can_be_recovered_from_article_page(self):
        since = datetime(2026, 6, 11, tzinfo=timezone.utc)
        entry = SimpleNamespace(
            title="Recovered date",
            link="https://example.com/recovered",
            summary="Short",
        )
        html = """
        <html><head>
          <script type="application/ld+json">
            {"@type":"Article","datePublished":"2026-06-11T02:00:00Z"}
          </script>
        </head><body>Full article body</body></html>
        """
        scraper = GenericRSSScraper()

        with patch(
            "app.services.scrapers.rss_generic.feedparser.parse",
            return_value=SimpleNamespace(bozo=False, entries=[entry]),
        ), patch("app.services.scrapers.rss_generic.httpx.AsyncClient") as client_cls:
            client = AsyncMock()
            response = SimpleNamespace(text="<rss></rss>", headers={}, raise_for_status=lambda: None)
            client.get.return_value = response
            client.__aenter__.return_value = client
            client.__aexit__.return_value = False
            client_cls.return_value = client

            with patch.object(
                scraper,
                "_fetch_article_page",
                AsyncMock(return_value=("Full recovered body", html, {})),
            ):
                articles = await scraper.fetch_articles(
                    "https://example.com/rss",
                    since=since,
                )

        self.assertEqual(len(articles), 1)
        self.assertEqual(articles[0].published_at, since + timedelta(hours=2))
        self.assertEqual(articles[0].date_source, "json_ld_date_published")
        self.assertEqual(articles[0].date_resolution_status, "resolved")

    async def test_old_feed_published_date_skips_page_fetch(self):
        since = datetime(2026, 6, 11, tzinfo=timezone.utc)
        entry = SimpleNamespace(
            title="Old article",
            link="https://example.com/old",
            summary="Short",
            published_parsed=(2026, 6, 10, 23, 59, 0, 0, 0, 0),
        )
        scraper = GenericRSSScraper()

        with patch(
            "app.services.scrapers.rss_generic.feedparser.parse",
            return_value=SimpleNamespace(bozo=False, entries=[entry]),
        ), patch("app.services.scrapers.rss_generic.httpx.AsyncClient") as client_cls:
            client = AsyncMock()
            response = SimpleNamespace(text="<rss></rss>", headers={}, raise_for_status=lambda: None)
            client.get.return_value = response
            client.__aenter__.return_value = client
            client.__aexit__.return_value = False
            client_cls.return_value = client

            page_fetch = AsyncMock()
            with patch.object(scraper, "_fetch_article_page", page_fetch):
                articles = await scraper.fetch_articles(
                    "https://example.com/rss",
                    since=since,
                )

        self.assertEqual(articles, [])
        page_fetch.assert_not_awaited()


class ScraperRegistryTests(unittest.TestCase):
    def test_hacker_news_scraper_registers_without_runtime_annotation_errors(self):
        registry = build_default_registry()
        scraper = registry.get_scraper("Hacker News", "https://news.ycombinator.com/rss")

        self.assertIsInstance(scraper, HackerNewsScraper)


class LexicalRetrievalTests(unittest.TestCase):
    def test_intent_description_adds_lexical_terms_and_phrases(self):
        terms = extract_all_keywords(
            keywords=["AI safety"],
            topic="Foundation models",
            intent_description="Track clinical trials and electric vehicles policy.",
        )

        self.assertIn("ai safety", terms)
        self.assertIn("clinical trial", terms)
        self.assertIn("electric vehicle", terms)

    def test_exclusions_are_precise_not_substring_matches(self):
        articles = [
            {
                "title": "Artificial intelligence policy",
                "raw_content": "AI regulation and safety",
                "tags": [],
            },
            {
                "title": "Modern art market",
                "raw_content": "Art auction results",
                "tags": [],
            },
        ]

        filtered = BM25Filter(
            keywords=["artificial intelligence"],
            exclusion_keywords=["art"],
        ).filter(articles)

        self.assertEqual([article["title"] for article in filtered], ["Artificial intelligence policy"])

    def test_bm25_accepts_hyphenated_and_plural_variants(self):
        articles = [
            {
                "title": "Electric-vehicle adoption accelerates",
                "raw_content": "Automakers expand charging networks.",
                "tags": [],
            },
            {
                "title": "Garden planning calendar",
                "raw_content": "Soil watering and flowers.",
                "tags": [],
            },
        ]

        filtered = BM25Filter(
            keywords=["electric vehicles"],
            exclusion_keywords=[],
        ).filter(articles)

        self.assertEqual([article["title"] for article in filtered], ["Electric-vehicle adoption accelerates"])


class FilterPipelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_bm25_passes_broad_candidate_set_to_llm(self):
        articles = [
            {
                "title": f"AI safety governance update {index}",
                "raw_content": "AI safety policy model governance regulation",
                "tags": ["AI"],
            }
            for index in range(25)
        ]
        pipeline = FilterPipeline(
            intent_context="Track AI safety governance",
            keywords=["AI safety", "model governance"],
            exclusion_keywords=[],
        )
        fake_llm = FakeLLMFilter()
        pipeline.llm_filter = fake_llm
        pipeline.semantic_filter = StaticSemanticFilter([])

        result = await pipeline.run(articles)

        self.assertEqual(len(fake_llm.seen_articles), 20)
        self.assertEqual(result, fake_llm.seen_articles)

    async def test_semantic_retrieval_rescues_non_bm25_candidate(self):
        lexical_article = {
            "title": "AI safety governance update",
            "url": "https://example.com/lexical",
            "raw_content": "AI safety policy model governance regulation",
            "tags": ["AI"],
        }
        semantic_article = {
            "title": "Frontier model oversight framework",
            "url": "https://example.com/semantic",
            "raw_content": "New rules for high capability model developers",
            "tags": ["policy"],
            "semantic_score": 0.82,
            "semantic_rank": 1,
        }
        pipeline = FilterPipeline(
            intent_context="Track AI safety governance",
            keywords=["AI safety"],
            exclusion_keywords=[],
        )
        fake_llm = FakeLLMFilter()
        pipeline.llm_filter = fake_llm
        pipeline.semantic_filter = StaticSemanticFilter([semantic_article])

        result = await pipeline.run([lexical_article, semantic_article])

        self.assertEqual(
            {article["url"] for article in fake_llm.seen_articles},
            {"https://example.com/lexical", "https://example.com/semantic"},
        )
        self.assertIn("semantic", semantic_article["retrieval_channels"])
        self.assertEqual(result, fake_llm.seen_articles)

    async def test_bm25_and_semantic_overlap_dedupes_before_llm(self):
        article = {
            "title": "AI safety governance update",
            "url": "https://example.com/shared",
            "raw_content": "AI safety policy model governance regulation",
            "tags": ["AI"],
            "semantic_score": 0.9,
            "semantic_rank": 1,
        }
        pipeline = FilterPipeline(
            intent_context="Track AI safety governance",
            keywords=["AI safety"],
            exclusion_keywords=[],
        )
        fake_llm = FakeLLMFilter()
        pipeline.llm_filter = fake_llm
        pipeline.semantic_filter = StaticSemanticFilter([article])

        await pipeline.run([article])

        self.assertEqual(len(fake_llm.seen_articles), 1)
        self.assertEqual(
            set(fake_llm.seen_articles[0]["retrieval_channels"]),
            {"bm25", "semantic"},
        )

    async def test_llm_relevance_failure_does_not_pass_all_candidates(self):
        articles = [
            {
                "title": "AI safety governance update",
                "url": "https://example.com/lexical",
                "raw_content": "AI safety policy model governance regulation",
                "tags": ["AI"],
            }
        ]
        pipeline = FilterPipeline(
            intent_context="Track AI safety governance",
            keywords=["AI safety"],
            exclusion_keywords=[],
        )
        pipeline.semantic_filter = StaticSemanticFilter([])
        pipeline.llm_filter.score_and_filter = AsyncMock(
            side_effect=RuntimeError("LLM relevance scoring failed: HTTP 503")
        )

        with self.assertRaisesRegex(RuntimeError, "LLM relevance scoring failed"):
            await pipeline.run(articles)


class SummariserResilienceTests(unittest.IsolatedAsyncioTestCase):
    async def test_summariser_batches_large_article_sets_before_calling_gemini(self):
        articles = [
            {
                "title": f"Article {index}",
                "url": f"https://example.com/{index}",
                "raw_content": "AI infrastructure " * 20,
            }
            for index in range(10)
        ]
        settings = SimpleNamespace(
            GEMINI_API_KEY="test-key",
            GEMINI_SUMMARY_BATCH_SIZE=4,
            GEMINI_SUMMARY_ARTICLE_MAX_CHARS=1200,
            GEMINI_SUMMARY_MODELS="gemini-2.5-flash-lite",
            GEMINI_REQUEST_TIMEOUT_SECONDS=45.0,
            GEMINI_RETRY_ATTEMPTS=1,
            GEMINI_RETRY_BACKOFF_SECONDS=0,
        )

        async def fake_batch(batch, topic, intent_context, settings_obj):
            return [dict(article, summary="ok") for article in batch]

        with patch("app.services.summariser.get_settings", return_value=settings), \
             patch("app.services.summariser._summarise_batch", AsyncMock(side_effect=fake_batch)) as batch_mock:
            result = await summarise_articles(
                articles,
                topic="AI infra",
                intent_context="Track AI infra spending",
            )

        self.assertEqual(len(result), 10)
        self.assertEqual([len(call.args[0]) for call in batch_mock.call_args_list], [4, 4, 2])

    async def test_summariser_uses_generic_llm_models_with_flash_lite_first(self):
        from app.services.summariser import _summarise_batch

        articles = [
            {
                "title": "AI infrastructure update",
                "url": "https://example.com/ai",
                "raw_content": "AI infrastructure spending is rising.",
            }
        ]
        settings = SimpleNamespace(
            LLM_SUMMARY_MODELS="gemini-2.5-flash-lite,gemini-2.5-flash",
            LLM_REQUEST_TIMEOUT_SECONDS=30.0,
            LLM_RETRY_ATTEMPTS=1,
            LLM_RETRY_BACKOFF_SECONDS=0,
            LLM_SUMMARY_ARTICLE_MAX_CHARS=1200,
        )

        with patch(
            "app.services.summariser.generate_json",
            AsyncMock(return_value=[SummaryResult(index=1, summary="Useful update", exclude=False)]),
        ) as generate_mock:
            result = await _summarise_batch(
                articles,
                topic="AI infra",
                intent_context="Track AI infrastructure spending",
                settings=settings,
            )

        self.assertEqual(result[0]["summary"], "Useful update")
        config = generate_mock.call_args.kwargs["config"]
        self.assertEqual(config.models, ["gemini-2.5-flash-lite", "gemini-2.5-flash"])


class LLMProviderTests(unittest.IsolatedAsyncioTestCase):
    def test_configured_models_parses_fallback_chain(self):
        self.assertEqual(
            configured_models(" gemini-2.5-flash-lite, gemini-2.5-flash "),
            ["gemini-2.5-flash-lite", "gemini-2.5-flash"],
        )

    async def test_relevance_filter_uses_generic_llm_models_with_flash_lite_first(self):
        settings = SimpleNamespace(
            LLM_RELEVANCE_MODELS="gemini-2.5-flash-lite,gemini-2.5-flash",
            LLM_REQUEST_TIMEOUT_SECONDS=30.0,
            LLM_RETRY_ATTEMPTS=1,
            LLM_RETRY_BACKOFF_SECONDS=0,
        )
        filter_ = LLMRelevanceFilter("Track AI safety")

        with patch(
            "app.services.filters.llm_relevance.generate_json",
            AsyncMock(return_value=[RelevanceScore(index=1, score=8, reason="Matches intent")]),
        ) as generate_mock:
            scores = await filter_._call_llm("prompt", expected_count=1, settings=settings)

        self.assertEqual(scores[1]["score"], 8)
        config = generate_mock.call_args.kwargs["config"]
        self.assertEqual(config.models, ["gemini-2.5-flash-lite", "gemini-2.5-flash"])

    def test_worker_disables_implicit_arq_retries_for_digest_jobs(self):
        from worker import WorkerSettings

        self.assertGreaterEqual(WorkerSettings.job_timeout, 900)
        self.assertEqual(WorkerSettings.max_tries, 1)


class SemanticRetrievalTests(unittest.IsolatedAsyncioTestCase):
    async def test_semantic_retriever_scores_and_filters_with_injected_model(self):
        articles = [
            {
                "title": "Frontier model oversight framework",
                "raw_content": "New governance rules for model developers.",
                "tags": ["policy"],
            },
            {
                "title": "Quarterly chip revenue",
                "raw_content": "Earnings report and sales growth.",
                "tags": ["finance"],
            },
        ]
        retriever = SemanticRetriever(
            query_text="AI governance oversight",
            model=FakeEmbeddingModel(),
            top_k=5,
            min_score=0.5,
        )

        result = await retriever.retrieve(articles)

        self.assertEqual([article["title"] for article in result], ["Frontier model oversight framework"])
        self.assertEqual(result[0]["semantic_rank"], 1)
        self.assertGreaterEqual(result[0]["semantic_score"], 0.5)


if __name__ == "__main__":
    unittest.main()

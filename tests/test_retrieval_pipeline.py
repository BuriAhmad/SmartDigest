import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.models.digest import Digest
from app.models.digest_item import DigestItem
from app.models.pipeline_event import PipelineEvent
from app.services.publication_dates import STATUS_UPDATED_ONLY, resolve_publication_date
from app.services.fetcher import fetch_articles
from app.services.filters import FilterPipeline
from app.services.filters.bm25 import BM25Filter
from app.services.filters.semantic import SemanticRetriever
from app.services.intent import extract_all_keywords
from app.services.scrapers.rss_generic import GenericRSSScraper
from app.services.scheduler import run_pipeline


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


class FakeSession:
    def __init__(self, briefing):
        self.briefing = briefing
        self.added = []
        self.committed = False
        self.flushed = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute(self, _query):
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
    )


class RetrievalPipelineTests(unittest.IsolatedAsyncioTestCase):
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

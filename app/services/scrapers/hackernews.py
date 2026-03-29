"""Hacker News scraper — purpose-built for news.ycombinator.com.

HN's RSS feed only provides titles and links — almost no content.
This scraper fetches linked articles and extracts full text via trafilatura,
plus HN-specific metadata (points, comment count from HN API if available).
"""

import asyncio
from datetime import datetime, timezone
from typing import Dict, List, Optional

import feedparser
import httpx
import structlog

from app.services.scrapers.base import BaseScraper, RawArticle

logger = structlog.get_logger()


class HackerNewsScraper(BaseScraper):
    """Scraper optimised for Hacker News.

    Strategy:
    1. Parse the HN RSS feed for titles + links.
    2. For each article, fetch the linked page and extract full text
       with trafilatura.
    3. If the linked page fails, fall back to the HN comments page content.
    4. Tag articles with [Show HN], [Ask HN], [Launch HN] etc.
    """

    MAX_ARTICLES = 15
    CONTENT_MAX_CHARS = 4000
    FETCH_TIMEOUT = 12.0
    CONCURRENT_FETCHES = 5  # Don't hammer target sites

    async def fetch_articles(
        self,
        source_url: str,
        source_name: str = "Hacker News",
        scraper_config: Optional[Dict] = None,
        since: Optional[datetime] = None,
    ) -> List[RawArticle]:
        log = logger.bind(source="hackernews")

        # Step 1: Fetch and parse the RSS feed
        try:
            async with httpx.AsyncClient(
                timeout=self.FETCH_TIMEOUT,
                follow_redirects=True,
                headers={"User-Agent": "SmartDigest/2.0 (RSS Reader)"},
            ) as client:
                resp = await client.get(source_url)
                resp.raise_for_status()
        except Exception as exc:
            log.error("hn_scraper.feed_fetch_failed", error=str(exc))
            return []

        parsed = feedparser.parse(resp.text)
        if parsed.bozo and not parsed.entries:
            log.warning("hn_scraper.parse_error")
            return []

        entries = parsed.entries[:self.MAX_ARTICLES]

        # Step 2: Fetch full article content concurrently (with semaphore)
        sem = asyncio.Semaphore(self.CONCURRENT_FETCHES)
        tasks = [
            self._process_entry(entry, sem, since)
            for entry in entries
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        articles = []
        for result in results:
            if isinstance(result, RawArticle):
                articles.append(result)
            elif isinstance(result, Exception):
                log.warning("hn_scraper.entry_error", error=str(result))

        log.info("hn_scraper.done", articles=len(articles))
        return articles

    async def _process_entry(
        self,
        entry,
        sem: asyncio.Semaphore,
        since: Optional[datetime],
    ) -> Optional[RawArticle]:
        """Process a single HN RSS entry: extract metadata and fetch full content."""
        title = getattr(entry, "title", "Untitled")
        link = getattr(entry, "link", "")
        comments_url = getattr(entry, "comments", "")

        # Parse date
        published_at = None
        for attr in ("published_parsed", "updated_parsed"):
            parsed = getattr(entry, attr, None)
            if parsed:
                try:
                    published_at = datetime(*parsed[:6], tzinfo=timezone.utc)
                except Exception:
                    pass
                break

        if since and published_at and published_at < since:
            return None

        # Detect HN post type from title
        tags = self._detect_hn_tags(title)

        # Fetch the actual article content
        async with sem:
            content = await self._fetch_article_content(link)

        if not content:
            # For Ask HN / Show HN, the "article" is the HN page itself
            if comments_url and comments_url != link:
                async with sem:
                    content = await self._fetch_article_content(comments_url)

        if not content:
            content = f"[Title only] {title}"

        # Truncate
        if len(content) > self.CONTENT_MAX_CHARS:
            content = content[:self.CONTENT_MAX_CHARS] + "..."

        return RawArticle(
            title=title,
            url=link,
            source_url="https://news.ycombinator.com/rss",
            raw_content=content,
            published_at=published_at,
            tags=tags,
        )

    @staticmethod
    def _detect_hn_tags(title: str) -> List[str]:
        """Detect HN-specific post types from the title."""
        tags = []
        title_lower = title.lower()
        if title_lower.startswith("show hn"):
            tags.append("Show HN")
        elif title_lower.startswith("ask hn"):
            tags.append("Ask HN")
        elif title_lower.startswith("tell hn"):
            tags.append("Tell HN")
        elif title_lower.startswith("launch hn"):
            tags.append("Launch HN")
        return tags

    @staticmethod
    async def _fetch_article_content(url: str) -> Optional[str]:
        """Fetch a URL and extract article text with trafilatura."""
        if not url:
            return None

        try:
            async with httpx.AsyncClient(
                timeout=12.0,
                follow_redirects=True,
                headers={
                    "User-Agent": "Mozilla/5.0 (compatible; SmartDigest/2.0)",
                    "Accept": "text/html,application/xhtml+xml",
                },
            ) as client:
                resp = await client.get(url)
                resp.raise_for_status()

                content_type = resp.headers.get("content-type", "")
                if "text/html" not in content_type and "xhtml" not in content_type:
                    return None

                import trafilatura
                text = trafilatura.extract(
                    resp.text,
                    url=url,
                    include_comments=False,
                    include_tables=False,
                    favor_recall=True,
                )
                return text

        except Exception:
            return None

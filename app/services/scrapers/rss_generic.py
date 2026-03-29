"""Generic RSS scraper — the default fallback for any RSS/Atom feed.

Uses feedparser for parsing and trafilatura for full-text extraction
when the RSS content field is too short or HTML-heavy.
"""

import re
from datetime import datetime, timezone
from typing import Dict, List, Optional

import feedparser
import httpx
import structlog

from app.services.scrapers.base import BaseScraper, RawArticle

logger = structlog.get_logger()


def _strip_html(text: str) -> str:
    """Remove HTML tags and normalise whitespace."""
    cleaned = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", cleaned).strip()


def _try_trafilatura(url: str, html: str) -> Optional[str]:
    """Attempt full-text extraction with trafilatura. Returns None on failure."""
    try:
        import trafilatura
        result = trafilatura.extract(
            html,
            url=url,
            include_comments=False,
            include_tables=False,
            favor_recall=True,
        )
        return result
    except Exception:
        return None


class GenericRSSScraper(BaseScraper):
    """Scraper for standard RSS/Atom feeds.

    Strategy:
    1. Fetch the RSS feed XML.
    2. Parse with feedparser.
    3. For each entry, check if content is substantial (>200 chars after stripping).
    4. If content is thin, fetch the article URL and extract with trafilatura.
    5. Return RawArticle list.
    """

    MAX_ARTICLES = 15
    CONTENT_MAX_CHARS = 4000
    FETCH_TIMEOUT = 15.0

    # Minimum content length before we try trafilatura on the full page
    MIN_CONTENT_LENGTH = 200

    async def fetch_articles(
        self,
        source_url: str,
        source_name: str = "",
        scraper_config: Optional[Dict] = None,
        since: Optional[datetime] = None,
    ) -> List[RawArticle]:
        log = logger.bind(source=source_name, url=source_url)
        articles: List[RawArticle] = []

        try:
            async with httpx.AsyncClient(
                timeout=self.FETCH_TIMEOUT,
                follow_redirects=True,
                headers={"User-Agent": "SmartDigest/2.0 (RSS Reader)"},
            ) as client:
                resp = await client.get(source_url)
                resp.raise_for_status()

            parsed = feedparser.parse(resp.text)
            if parsed.bozo and not parsed.entries:
                log.warning("rss_scraper.parse_error", error=str(parsed.bozo_exception))
                return []

            for entry in parsed.entries[:self.MAX_ARTICLES]:
                # Parse published date
                published_at = self._parse_date(entry)

                # Skip articles older than `since`
                if since and published_at and published_at < since:
                    continue

                # Extract content from the RSS entry itself
                raw_content = self._extract_entry_content(entry)
                clean_content = _strip_html(raw_content)

                title = getattr(entry, "title", "Untitled")
                link = getattr(entry, "link", "")

                # Extract tags from the entry
                tags = self._extract_tags(entry)

                # If content is thin, try fetching the full article
                if len(clean_content) < self.MIN_CONTENT_LENGTH and link:
                    full_text = await self._fetch_full_article(client=None, url=link)
                    if full_text and len(full_text) > len(clean_content):
                        clean_content = full_text

                # Truncate to max chars
                if len(clean_content) > self.CONTENT_MAX_CHARS:
                    clean_content = clean_content[:self.CONTENT_MAX_CHARS] + "..."

                articles.append(RawArticle(
                    title=title,
                    url=link,
                    source_url=source_url,
                    raw_content=clean_content,
                    published_at=published_at,
                    author=getattr(entry, "author", None),
                    tags=tags,
                ))

            log.info("rss_scraper.done", articles=len(articles))

        except httpx.TimeoutException:
            log.warning("rss_scraper.timeout")
        except Exception as exc:
            log.error("rss_scraper.error", error=str(exc))

        return articles

    @staticmethod
    def _parse_date(entry) -> Optional[datetime]:
        """Extract published datetime from a feedparser entry."""
        for attr in ("published_parsed", "updated_parsed"):
            parsed = getattr(entry, attr, None)
            if parsed:
                try:
                    return datetime(*parsed[:6], tzinfo=timezone.utc)
                except Exception:
                    pass
        return None

    @staticmethod
    def _extract_entry_content(entry) -> str:
        """Get the best content field from a feedparser entry."""
        if hasattr(entry, "content") and entry.content:
            return entry.content[0].get("value", "")
        if hasattr(entry, "summary"):
            return entry.summary or ""
        if hasattr(entry, "description"):
            return entry.description or ""
        return ""

    @staticmethod
    def _extract_tags(entry) -> List[str]:
        """Extract tag/category labels from a feedparser entry."""
        tags = []
        if hasattr(entry, "tags"):
            for tag in entry.tags:
                term = tag.get("term") or tag.get("label")
                if term:
                    tags.append(term.strip())
        return tags

    async def _fetch_full_article(self, client, url: str) -> Optional[str]:
        """Fetch the full article HTML and extract text with trafilatura."""
        try:
            async with httpx.AsyncClient(
                timeout=self.FETCH_TIMEOUT,
                follow_redirects=True,
                headers={"User-Agent": "SmartDigest/2.0"},
            ) as c:
                resp = await c.get(url)
                resp.raise_for_status()
                return _try_trafilatura(url, resp.text)
        except Exception:
            return None

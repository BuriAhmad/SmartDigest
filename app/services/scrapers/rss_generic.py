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

from app.services.publication_dates import (
    STATUS_RESOLVED,
    is_newer_than_window,
    resolve_publication_date,
)
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

            date_counts = {
                "feed_resolved": 0,
                "page_recovered": 0,
                "unresolved": 0,
                "skipped_old": 0,
                "skipped_unknown_date": 0,
            }
            for entry in parsed.entries:
                date_result = resolve_publication_date(feed_entry=entry)

                if since and date_result.published_at and date_result.published_at <= since:
                    date_counts["skipped_old"] += 1
                    continue

                # Extract content from the RSS entry itself
                raw_content = self._extract_entry_content(entry)
                clean_content = _strip_html(raw_content)

                title = getattr(entry, "title", "Untitled")
                link = getattr(entry, "link", "")

                # Extract tags from the entry
                tags = self._extract_tags(entry)

                needs_page_fetch = bool(link) and (
                    len(clean_content) < self.MIN_CONTENT_LENGTH
                    or date_result.published_at is None
                )
                if needs_page_fetch:
                    full_text, page_html, response_headers = await self._fetch_article_page(link)
                    if page_html:
                        date_result = resolve_publication_date(
                            feed_entry=entry,
                            html=page_html,
                            url=link,
                            response_headers=response_headers,
                        )
                    if full_text and len(full_text) > len(clean_content):
                        clean_content = full_text

                if since and not is_newer_than_window(date_result, since):
                    if date_result.published_at:
                        date_counts["skipped_old"] += 1
                    else:
                        date_counts["skipped_unknown_date"] += 1
                    continue

                if date_result.status == STATUS_RESOLVED:
                    if date_result.source.startswith("feed_"):
                        date_counts["feed_resolved"] += 1
                    else:
                        date_counts["page_recovered"] += 1
                else:
                    date_counts["unresolved"] += 1

                # Truncate to max chars
                if len(clean_content) > self.CONTENT_MAX_CHARS:
                    clean_content = clean_content[:self.CONTENT_MAX_CHARS] + "..."

                articles.append(RawArticle(
                    title=title,
                    url=link,
                    source_url=source_url,
                    raw_content=clean_content,
                    published_at=date_result.published_at,
                    updated_at=date_result.updated_at,
                    date_source=date_result.source,
                    date_confidence=date_result.confidence,
                    date_resolution_status=date_result.status,
                    date_candidates=[candidate.to_dict() for candidate in date_result.candidates],
                    author=getattr(entry, "author", None),
                    tags=tags,
                ))

            log.info("rss_scraper.done", articles=len(articles), **date_counts)

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

    async def _fetch_article_page(self, url: str) -> tuple[Optional[str], Optional[str], Dict[str, str]]:
        """Fetch the article page once for both full text and date metadata."""
        try:
            async with httpx.AsyncClient(
                timeout=self.FETCH_TIMEOUT,
                follow_redirects=True,
                headers={"User-Agent": "SmartDigest/2.0"},
            ) as c:
                resp = await c.get(url)
                resp.raise_for_status()
                return _try_trafilatura(url, resp.text), resp.text, dict(resp.headers)
        except Exception:
            return None, None, {}

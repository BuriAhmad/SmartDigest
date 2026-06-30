"""Indie Hackers scraper for its HTML listing and post pages."""

import asyncio
from datetime import datetime
from html.parser import HTMLParser
from typing import Dict, List, Optional
from urllib.parse import urljoin, urlparse

import httpx
import structlog

from app.services.publication_dates import is_newer_than_window, resolve_publication_date
from app.services.scrapers.base import BaseScraper, RawArticle

logger = structlog.get_logger()


class _ListingParser(HTMLParser):
    """Collect unique Indie Hackers post links from a listing page."""

    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.base_url = base_url
        self.links: List[str] = []
        self._seen = set()

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag != "a":
            return
        href = dict(attrs).get("href", "")
        absolute = urljoin(self.base_url, href)
        parsed = urlparse(absolute)
        if parsed.netloc not in {"indiehackers.com", "www.indiehackers.com"}:
            return
        if not parsed.path.startswith("/post/"):
            return
        canonical = f"https://www.indiehackers.com{parsed.path.rstrip('/')}"
        if canonical not in self._seen:
            self._seen.add(canonical)
            self.links.append(canonical)


class _TitleParser(HTMLParser):
    """Extract the first post heading, with the page title as fallback."""

    def __init__(self) -> None:
        super().__init__()
        self._capture: Optional[str] = None
        self._parts: Dict[str, List[str]] = {"h1": [], "title": []}

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in self._parts and not self._parts[tag]:
            self._capture = tag

    def handle_endtag(self, tag: str) -> None:
        if self._capture == tag:
            self._capture = None

    def handle_data(self, data: str) -> None:
        if self._capture:
            self._parts[self._capture].append(data)

    @property
    def title(self) -> str:
        for tag in ("h1", "title"):
            value = " ".join(" ".join(self._parts[tag]).split())
            if value:
                return value.removesuffix(" - Indie Hackers")
        return "Untitled"


class IndieHackersScraper(BaseScraper):
    """Discover posts from Indie Hackers, then fetch each post's full text."""

    CONTENT_MAX_CHARS = 4000
    FETCH_TIMEOUT = 15.0
    DEFAULT_MAX_ITEMS = 20
    DEFAULT_CONCURRENT_FETCHES = 4

    async def fetch_articles(
        self,
        source_url: str,
        source_name: str = "Indie Hackers",
        scraper_config: Optional[Dict] = None,
        since: Optional[datetime] = None,
    ) -> List[RawArticle]:
        config = scraper_config or {}
        max_items = max(1, min(int(config.get("max_items", self.DEFAULT_MAX_ITEMS)), 50))
        concurrency = max(
            1,
            min(int(config.get("concurrent_fetches", self.DEFAULT_CONCURRENT_FETCHES)), 8),
        )
        log = logger.bind(source=source_name or "Indie Hackers", url=source_url)

        try:
            listing_html = await self._fetch_html(source_url)
        except Exception as exc:
            log.error("indiehackers.listing_fetch_failed", error=str(exc))
            return []

        parser = _ListingParser(source_url)
        parser.feed(listing_html)
        post_urls = parser.links[:max_items]
        semaphore = asyncio.Semaphore(concurrency)
        results = await asyncio.gather(
            *(self._process_post(url, source_url, since, semaphore) for url in post_urls),
            return_exceptions=True,
        )

        articles: List[RawArticle] = []
        for result in results:
            if isinstance(result, RawArticle):
                articles.append(result)
            elif isinstance(result, Exception):
                log.warning("indiehackers.post_failed", error=str(result))

        log.info(
            "indiehackers.done",
            discovered=len(parser.links),
            attempted=len(post_urls),
            articles=len(articles),
        )
        return articles

    async def _process_post(
        self,
        post_url: str,
        source_url: str,
        since: Optional[datetime],
        semaphore: asyncio.Semaphore,
    ) -> Optional[RawArticle]:
        async with semaphore:
            try:
                html, headers = await self._fetch_html_with_headers(post_url)
            except Exception:
                return None

        date_result = resolve_publication_date(
            html=html,
            url=post_url,
            response_headers=headers,
        )
        if since and not is_newer_than_window(date_result, since):
            return None

        title_parser = _TitleParser()
        title_parser.feed(html)
        content = self._extract_content(post_url, html)
        if not content:
            return None
        if len(content) > self.CONTENT_MAX_CHARS:
            content = content[:self.CONTENT_MAX_CHARS] + "..."

        return RawArticle(
            title=title_parser.title,
            url=post_url,
            source_url=source_url,
            raw_content=content,
            published_at=date_result.published_at,
            updated_at=date_result.updated_at,
            date_source=date_result.source,
            date_confidence=date_result.confidence,
            date_resolution_status=date_result.status,
            date_candidates=[candidate.to_dict() for candidate in date_result.candidates],
            tags=["startups", "bootstrapping", "founders"],
        )

    async def _fetch_html(self, url: str) -> str:
        html, _headers = await self._fetch_html_with_headers(url)
        return html

    async def _fetch_html_with_headers(self, url: str) -> tuple[str, Dict[str, str]]:
        async with httpx.AsyncClient(
            timeout=self.FETCH_TIMEOUT,
            follow_redirects=True,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; SmartDigest/2.0)",
                "Accept": "text/html,application/xhtml+xml",
            },
        ) as client:
            response = await client.get(url)
            response.raise_for_status()
            content_type = response.headers.get("content-type", "text/html")
            if "html" not in content_type:
                raise ValueError(f"Unexpected content type: {content_type}")
            return response.text, dict(response.headers)

    @staticmethod
    def _extract_content(url: str, html: str) -> Optional[str]:
        try:
            import trafilatura

            return trafilatura.extract(
                html,
                url=url,
                include_comments=False,
                include_tables=False,
                favor_recall=True,
            )
        except Exception:
            return None

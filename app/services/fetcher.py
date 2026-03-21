"""FetcherService — fetches articles from RSS feeds using httpx + feedparser.

Returns structured article dicts ready for summarisation and storage.
"""

import asyncio
from datetime import datetime, timezone
from time import monotonic
from typing import Dict, List, Optional, Tuple

import feedparser
import httpx
import structlog

logger = structlog.get_logger()

# Timeout per feed fetch (seconds)
FETCH_TIMEOUT = 15.0

# Max articles to keep per feed
MAX_ARTICLES_PER_FEED = 10

# Total max articles across all feeds for one digest
MAX_ARTICLES_TOTAL = 20


async def fetch_feed(url: str) -> Tuple[List[Dict], int]:
    """Fetch a single RSS feed and parse it.

    Returns (articles, duration_ms).
    Each article is a dict with: title, url, source_url, raw_content, published_at.
    """
    log = logger.bind(feed_url=url)
    start = monotonic()
    articles: List[Dict] = []

    try:
        async with httpx.AsyncClient(
            timeout=FETCH_TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": "SmartDigest/1.0 (RSS Reader)"},
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()

        duration_ms = int((monotonic() - start) * 1000)

        # feedparser works synchronously on the response text
        parsed = feedparser.parse(resp.text)

        if parsed.bozo and not parsed.entries:
            log.warning("fetcher.parse_error", error=str(parsed.bozo_exception))
            return [], duration_ms

        for entry in parsed.entries[:MAX_ARTICLES_PER_FEED]:
            # Extract published date
            published_at = None
            if hasattr(entry, "published_parsed") and entry.published_parsed:
                try:
                    published_at = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
                except Exception:
                    pass
            elif hasattr(entry, "updated_parsed") and entry.updated_parsed:
                try:
                    published_at = datetime(*entry.updated_parsed[:6], tzinfo=timezone.utc)
                except Exception:
                    pass

            # Get content — prefer content field, fall back to summary/description
            raw_content = ""
            if hasattr(entry, "content") and entry.content:
                raw_content = entry.content[0].get("value", "")
            elif hasattr(entry, "summary"):
                raw_content = entry.summary or ""
            elif hasattr(entry, "description"):
                raw_content = entry.description or ""

            # Strip HTML tags for a cleaner text (simple approach)
            import re
            clean_content = re.sub(r"<[^>]+>", " ", raw_content)
            clean_content = re.sub(r"\s+", " ", clean_content).strip()

            # Truncate to ~2000 chars to keep Gemini context manageable
            if len(clean_content) > 2000:
                clean_content = clean_content[:2000] + "..."

            title = getattr(entry, "title", "Untitled")
            link = getattr(entry, "link", "")

            articles.append({
                "title": title,
                "url": link,
                "source_url": url,
                "raw_content": clean_content,
                "published_at": published_at,
            })

        log.info("fetcher.feed_parsed", articles=len(articles), duration_ms=duration_ms)
        return articles, duration_ms

    except httpx.TimeoutException:
        duration_ms = int((monotonic() - start) * 1000)
        log.warning("fetcher.timeout", duration_ms=duration_ms)
        return [], duration_ms

    except Exception as exc:
        duration_ms = int((monotonic() - start) * 1000)
        log.error("fetcher.error", error=str(exc), duration_ms=duration_ms)
        return [], duration_ms


async def fetch_articles(
    sources: List[str],
    topic: Optional[str] = None,
) -> Tuple[List[Dict], int]:
    """Fetch articles from multiple RSS feeds concurrently.

    Args:
        sources: List of RSS feed URLs.
        topic: Optional topic label for logging.

    Returns:
        (all_articles, total_duration_ms)
    """
    log = logger.bind(topic=topic, source_count=len(sources))
    log.info("fetcher.starting")

    start = monotonic()

    # Fetch all feeds concurrently
    tasks = [fetch_feed(url) for url in sources]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    all_articles: List[Dict] = []
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            log.error("fetcher.feed_exception", feed=sources[i], error=str(result))
            continue
        articles, _ = result
        all_articles.extend(articles)

    # Sort by published_at (newest first), then truncate
    all_articles.sort(
        key=lambda a: a.get("published_at") or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    all_articles = all_articles[:MAX_ARTICLES_TOTAL]

    total_ms = int((monotonic() - start) * 1000)
    log.info("fetcher.complete", total_articles=len(all_articles), duration_ms=total_ms)

    return all_articles, total_ms

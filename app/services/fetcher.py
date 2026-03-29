"""FetcherService — orchestrates per-source scrapers to fetch articles.

Uses the scraper registry to dispatch each source URL to the correct
scraper implementation. Falls back to the generic RSS scraper for
unknown sources.
"""

import asyncio
from datetime import datetime, timezone
from time import monotonic
from typing import Dict, List, Optional, Tuple

import structlog

from app.services.scrapers import build_default_registry

logger = structlog.get_logger()

# Total max articles across all feeds for one digest
MAX_ARTICLES_TOTAL = 20


async def fetch_articles(
    sources: List[str],
    topic: Optional[str] = None,
    source_metadata: Optional[Dict[str, Dict]] = None,
) -> Tuple[List[Dict], int]:
    """Fetch articles from multiple sources concurrently using per-source scrapers.

    Args:
        sources: List of source URLs (RSS feeds, etc.).
        topic: Optional topic label for logging.
        source_metadata: Optional dict mapping source_url to {name, scraper_config}.

    Returns:
        (all_articles, total_duration_ms)
    """
    log = logger.bind(topic=topic, source_count=len(sources))
    log.info("fetcher.starting")

    start = monotonic()
    registry = build_default_registry()
    metadata = source_metadata or {}

    async def _fetch_one_source(url: str) -> List[Dict]:
        """Fetch articles from a single source using the appropriate scraper."""
        meta = metadata.get(url, {})
        name = meta.get("name", "")
        config = meta.get("scraper_config")

        scraper = registry.get_scraper(name, url)
        try:
            raw_articles = await scraper.fetch_articles(
                source_url=url,
                source_name=name,
                scraper_config=config,
            )
            return [a.to_dict() for a in raw_articles]
        except Exception as exc:
            log.error("fetcher.scraper_error", source=name or url, error=str(exc))
            return []

    # Fetch all sources concurrently
    tasks = [_fetch_one_source(url) for url in sources]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    all_articles: List[Dict] = []
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            log.error("fetcher.source_exception", source=sources[i], error=str(result))
            continue
        all_articles.extend(result)

    # Sort by published_at (newest first), then truncate
    all_articles.sort(
        key=lambda a: a.get("published_at") or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    all_articles = all_articles[:MAX_ARTICLES_TOTAL]

    total_ms = int((monotonic() - start) * 1000)
    log.info("fetcher.complete", total_articles=len(all_articles), duration_ms=total_ms)

    return all_articles, total_ms

"""Base scraper interface.

All source-specific scrapers implement this interface. The fetcher
orchestrator calls `fetch_articles()` and gets back a uniform list of
RawArticle dicts.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional


@dataclass
class RawArticle:
    """Uniform article representation returned by all scrapers."""

    title: str
    url: str
    source_url: str
    raw_content: str
    published_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    date_source: str = "unknown"
    date_confidence: str = "low"
    date_resolution_status: str = "unresolved"
    date_candidates: List[Dict] = field(default_factory=list)
    author: Optional[str] = None
    tags: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            "title": self.title,
            "url": self.url,
            "source_url": self.source_url,
            "raw_content": self.raw_content,
            "published_at": self.published_at,
            "updated_at": self.updated_at,
            "date_source": self.date_source,
            "date_confidence": self.date_confidence,
            "date_resolution_status": self.date_resolution_status,
            "date_candidates": self.date_candidates,
            "author": self.author,
            "tags": self.tags,
        }


class BaseScraper(ABC):
    """Abstract base for all source scrapers."""

    # Subclasses can override these defaults
    CONTENT_MAX_CHARS: int = 4000
    FETCH_TIMEOUT: float = 15.0

    @abstractmethod
    async def fetch_articles(
        self,
        source_url: str,
        source_name: str = "",
        scraper_config: Optional[Dict] = None,
        since: Optional[datetime] = None,
    ) -> List[RawArticle]:
        """Fetch articles from this source.

        Args:
            source_url: The feed/page URL to scrape.
            source_name: Human-readable source name.
            scraper_config: Optional source-specific config from DB.
            since: Only return articles newer than this datetime.

        Returns:
            List of RawArticle objects.
        """
        ...

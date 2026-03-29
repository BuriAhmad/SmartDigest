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
    author: Optional[str] = None
    tags: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            "title": self.title,
            "url": self.url,
            "source_url": self.source_url,
            "raw_content": self.raw_content,
            "published_at": self.published_at,
            "author": self.author,
            "tags": self.tags,
        }


class BaseScraper(ABC):
    """Abstract base for all source scrapers."""

    # Subclasses can override these defaults
    MAX_ARTICLES: int = 15
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

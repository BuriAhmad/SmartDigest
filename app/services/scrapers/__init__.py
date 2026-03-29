"""Scraper registry and base interface.

Maps source URLs/names to concrete scraper implementations.
Falls back to the generic RSS scraper for unconfigured sources.
"""

from typing import Dict, Optional, Type

from app.services.scrapers.base import BaseScraper


class ScraperRegistry:
    """Registry that maps source identifiers to scraper classes."""

    def __init__(self) -> None:
        self._by_name: Dict[str, Type[BaseScraper]] = {}
        self._by_url_pattern: Dict[str, Type[BaseScraper]] = {}
        self._default: Optional[Type[BaseScraper]] = None

    def register_by_name(self, name: str, scraper_cls: Type[BaseScraper]) -> None:
        """Register a scraper for a source name (case-insensitive)."""
        self._by_name[name.lower()] = scraper_cls

    def register_by_url(self, url_contains: str, scraper_cls: Type[BaseScraper]) -> None:
        """Register a scraper for URLs containing a given pattern."""
        self._by_url_pattern[url_contains.lower()] = scraper_cls

    def set_default(self, scraper_cls: Type[BaseScraper]) -> None:
        """Set the fallback scraper for unrecognised sources."""
        self._default = scraper_cls

    def get_scraper(self, source_name: str, source_url: str) -> BaseScraper:
        """Look up the best scraper for a source. Returns an instance."""
        # Check by name first
        cls = self._by_name.get(source_name.lower())
        if cls:
            return cls()

        # Check by URL pattern
        url_lower = source_url.lower()
        for pattern, cls in self._by_url_pattern.items():
            if pattern in url_lower:
                return cls()

        # Fall back to default (generic RSS)
        if self._default:
            return self._default()

        raise ValueError(f"No scraper registered for source: {source_name} ({source_url})")


def build_default_registry() -> ScraperRegistry:
    """Build and return the production scraper registry with all known scrapers."""
    from app.services.scrapers.rss_generic import GenericRSSScraper
    from app.services.scrapers.hackernews import HackerNewsScraper

    registry = ScraperRegistry()
    registry.set_default(GenericRSSScraper)

    # Per-source scrapers
    registry.register_by_name("Hacker News", HackerNewsScraper)
    registry.register_by_url("news.ycombinator.com", HackerNewsScraper)

    return registry

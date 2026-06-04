"""CLI utilities for SmartDigest.

Usage:
    python -m app.cli seed_sources
"""

import asyncio
import sys

import structlog

from app.database import async_session
from app.models.curated_source import CuratedSource

logger = structlog.get_logger()

# Curated sources with metadata for per-source scraping
SEED_SOURCES = [
    {
        "name": "Hacker News",
        "url": "https://news.ycombinator.com/rss",
        "source_type": "rss",
        "category": "tech",
        "tags": ["startups", "programming", "tech", "open-source", "AI"],
        "description": "Community-curated tech news. Broad coverage of programming, startups, AI, and technology.",
    },
    {
        "name": "TechCrunch",
        "url": "https://techcrunch.com/feed/",
        "source_type": "rss",
        "category": "tech",
        "tags": ["startups", "funding", "venture-capital", "product-launches", "AI"],
        "description": "Startup and technology news. Strong on funding rounds, product launches, and industry trends.",
    },
    {
        "name": "MIT Tech Review",
        "url": "https://www.technologyreview.com/feed/",
        "source_type": "rss",
        "category": "science-tech",
        "tags": ["AI", "biotech", "climate", "research", "deep-tech"],
        "description": "In-depth technology journalism. Focuses on emerging tech, AI research, and scientific breakthroughs.",
    },
    {
        "name": "The Verge",
        "url": "https://www.theverge.com/rss/index.xml",
        "source_type": "rss",
        "category": "tech",
        "tags": ["consumer-tech", "gadgets", "policy", "social-media", "AI"],
        "description": "Consumer technology and digital culture. Covers gadgets, platforms, and tech policy.",
    },
    {
        "name": "Wired",
        "url": "https://www.wired.com/feed/rss",
        "source_type": "rss",
        "category": "tech",
        "tags": ["science", "culture", "security", "AI", "business"],
        "description": "Technology, science, and culture. Long-form journalism on how tech shapes society.",
    },
    {
        "name": "Ars Technica",
        "url": "https://feeds.arstechnica.com/arstechnica/index",
        "source_type": "rss",
        "category": "tech",
        "tags": ["hardware", "software", "science", "policy", "security"],
        "description": "Deep technical coverage. Strong on hardware, software, science, and IT policy.",
    },
    {
        "name": "VentureBeat",
        "url": "https://venturebeat.com/feed/",
        "source_type": "rss",
        "category": "tech",
        "tags": ["AI", "enterprise", "gaming", "machine-learning", "data"],
        "description": "Enterprise tech and AI news. Covers AI/ML, enterprise software, and gaming tech.",
    },
    {
        "name": "InfoQ",
        "url": "https://www.infoq.com/feed/",
        "source_type": "rss",
        "category": "software-engineering",
        "tags": ["architecture", "devops", "cloud", "programming", "AI"],
        "description": "Software engineering news. Deep dives into architecture, DevOps, cloud, and practices.",
    },
    {
        "name": "Dev.to",
        "url": "https://dev.to/feed",
        "source_type": "rss",
        "category": "software-engineering",
        "tags": ["programming", "tutorials", "web-dev", "open-source", "career"],
        "description": "Developer community platform. Tutorials, opinion pieces, and dev career content.",
    },
    {
        "name": "Simon Willison",
        "url": "https://simonwillison.net/atom/everything/",
        "source_type": "rss",
        "category": "tech",
        "tags": ["AI", "LLM", "python", "open-source", "data"],
        "description": "Simon Willison's blog. Focused on AI/LLM tools, Python, open data, and developer tools.",
    },
]


async def _seed_sources() -> None:
    """Seed the curated_sources table with enriched source metadata."""
    from sqlalchemy import select

    async with async_session() as session:
        result = await session.execute(select(CuratedSource.url))
        existing_urls = {row[0] for row in result.all()}

        added = 0
        for source_data in SEED_SOURCES:
            if source_data["url"] not in existing_urls:
                session.add(CuratedSource(
                    name=source_data["name"],
                    url=source_data["url"],
                    source_type=source_data.get("source_type", "rss"),
                    category=source_data.get("category"),
                    tags=source_data.get("tags", []),
                    description=source_data.get("description"),
                ))
                added += 1

        await session.commit()

    print(f"\n✅ Seeded {added} sources ({len(existing_urls)} already existed).")
    print(f"   Total curated sources: {len(existing_urls) + added}\n")


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python -m app.cli <command>")
        print("Commands:")
        print("  seed_sources  — Seed curated RSS sources")
        sys.exit(1)

    command = sys.argv[1]

    if command == "seed_sources":
        asyncio.run(_seed_sources())
    else:
        print(f"Unknown command: {command}")
        sys.exit(1)


if __name__ == "__main__":
    main()

"""CLI utilities for SmartDigest.

Usage:
    python -m app.cli create_key
    python -m app.cli seed_sources
"""

import asyncio
import hashlib
import secrets
import sys

import structlog

from app.config import get_settings
from app.database import async_session
from app.models.api_key import ApiKey
from app.models.curated_source import CuratedSource

logger = structlog.get_logger()

# Curated RSS sources from the spec (Section D10)
SEED_SOURCES = [
    ("Hacker News", "https://news.ycombinator.com/rss"),
    ("TechCrunch", "https://techcrunch.com/feed/"),
    ("MIT Tech Review", "https://www.technologyreview.com/feed/"),
    ("The Verge", "https://www.theverge.com/rss/index.xml"),
    ("Wired", "https://www.wired.com/feed/rss"),
    ("Ars Technica", "https://feeds.arstechnica.com/arstechnica/index"),
    ("VentureBeat", "https://venturebeat.com/feed/"),
    ("InfoQ", "https://www.infoq.com/feed/"),
    ("Dev.to", "https://dev.to/feed"),
    ("Simon Willison", "https://simonwillison.net/atom/everything/"),
]


async def _create_key() -> None:
    """Generate an API key, store its hash, and print the plaintext."""
    raw_key = secrets.token_hex(32)
    prefix = raw_key[:4]
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()

    async with async_session() as session:
        api_key = ApiKey(prefix=prefix, key_hash=key_hash)
        session.add(api_key)
        await session.commit()

    print(f"\n✅ API Key created!")
    print(f"   Key:    {raw_key}")
    print(f"   Prefix: {prefix}")
    print(f"\n⚠  Save this key — it will not be shown again.\n")


async def _seed_sources() -> None:
    """Seed the curated_sources table with RSS feeds from the spec."""
    from sqlalchemy import select

    async with async_session() as session:
        result = await session.execute(select(CuratedSource.rss_url))
        existing_urls = {row[0] for row in result.all()}

        added = 0
        for name, url in SEED_SOURCES:
            if url not in existing_urls:
                session.add(CuratedSource(name=name, rss_url=url))
                added += 1

        await session.commit()

    print(f"\n✅ Seeded {added} sources ({len(existing_urls)} already existed).")
    print(f"   Total curated sources: {len(existing_urls) + added}\n")


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python -m app.cli <command>")
        print("Commands:")
        print("  create_key    — Generate a new API key")
        print("  seed_sources  — Seed curated RSS sources")
        sys.exit(1)

    command = sys.argv[1]

    if command == "create_key":
        asyncio.run(_create_key())
    elif command == "seed_sources":
        asyncio.run(_seed_sources())
    else:
        print(f"Unknown command: {command}")
        sys.exit(1)


if __name__ == "__main__":
    main()

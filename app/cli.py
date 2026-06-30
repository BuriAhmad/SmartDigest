"""CLI utilities for SmartDigest.

Usage:
    python -m app.cli seed_sources
"""

import asyncio
import sys

import structlog

from app.database import async_session
from app.models.curated_source import CuratedSource
from app.source_catalog import SEED_SOURCES

logger = structlog.get_logger()

async def _seed_sources() -> None:
    """Add new sources and refresh metadata for existing catalog entries."""
    from sqlalchemy import select

    async with async_session() as session:
        result = await session.execute(select(CuratedSource))
        existing_by_url = {source.url: source for source in result.scalars().all()}

        added = 0
        updated = 0
        for source_data in SEED_SOURCES:
            values = {
                "name": source_data["name"],
                "source_type": source_data.get("source_type", "rss"),
                "category": source_data.get("category"),
                "tags": source_data.get("tags", []),
                "description": source_data.get("description"),
                "scraper_config": source_data.get("scraper_config", {}),
            }
            existing = existing_by_url.get(source_data["url"])
            if existing is None:
                session.add(CuratedSource(url=source_data["url"], **values))
                added += 1
                continue

            changed = False
            for field, value in values.items():
                if getattr(existing, field) != value:
                    setattr(existing, field, value)
                    changed = True
            updated += int(changed)

        await session.commit()

    print(f"\nSeeded {added} new sources and updated {updated} existing sources.")
    print(f"Catalog entries: {len(SEED_SOURCES)}\n")


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

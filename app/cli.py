"""CLI utilities for SmartDigest.

Usage:
    python -m app.cli create_user <email> <password> <name>
    python -m app.cli seed_sources
"""

import asyncio
import sys

import structlog

from app.config import get_settings
from app.database import async_session
from app.models.user import User
from app.models.curated_source import CuratedSource
from app.services.auth import hash_password

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


async def _create_user(email: str, password: str, name: str) -> None:
    """Create a new user account from the CLI."""
    from sqlalchemy import select

    async with async_session() as session:
        # Check if user already exists
        result = await session.execute(select(User).where(User.email == email))
        if result.scalar_one_or_none() is not None:
            print(f"\n❌ User with email {email} already exists.\n")
            return

        user = User(
            email=email,
            password_hash=hash_password(password),
            name=name,
        )
        session.add(user)
        await session.commit()

    print(f"\n✅ User created!")
    print(f"   Email: {email}")
    print(f"   Name:  {name}")
    print(f"\n   Log in at /login with your email and password.\n")


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
        print("  create_user <email> <password> <name>  — Create a user account")
        print("  seed_sources                           — Seed curated RSS sources")
        sys.exit(1)

    command = sys.argv[1]

    if command == "create_user":
        if len(sys.argv) < 5:
            print("Usage: python -m app.cli create_user <email> <password> <name>")
            sys.exit(1)
        email = sys.argv[2]
        password = sys.argv[3]
        name = " ".join(sys.argv[4:])
        asyncio.run(_create_user(email, password, name))
    elif command == "seed_sources":
        asyncio.run(_seed_sources())
    else:
        print(f"Unknown command: {command}")
        sys.exit(1)


if __name__ == "__main__":
    main()

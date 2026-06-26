"""Smoke test the Postgres URL used by SQLAlchemy asyncpg."""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import get_settings, prepare_asyncpg_database_url

EXPECTED_TABLES = {
    "users",
    "curated_sources",
    "briefings",
    "digests",
    "digest_items",
    "pipeline_events",
}


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check-tables",
        action="store_true",
        help="Verify core tables exist and curated sources have been seeded.",
    )
    args = parser.parse_args()

    load_dotenv()
    settings = get_settings()
    database_url, connect_args = prepare_asyncpg_database_url(settings.DATABASE_URL)
    parsed_url = make_url(database_url)

    print(f"DATABASE_URL dialect: {parsed_url.drivername}")
    print(f"DATABASE_URL host: {parsed_url.host or '<missing>'}")
    print(f"DATABASE_URL database: {parsed_url.database or '<missing>'}")
    print(f"DATABASE_URL user: {parsed_url.username or '<missing>'}")
    print(f"asyncpg ssl enabled: {bool(connect_args.get('ssl'))}")

    engine = create_async_engine(
        database_url,
        pool_pre_ping=True,
        connect_args=connect_args,
    )

    try:
        async with engine.connect() as connection:
            row = (
                await connection.execute(
                    text(
                        """
                        SELECT
                            current_database() AS database,
                            current_user AS role,
                            inet_server_addr()::text AS server_addr
                        """
                    )
                )
            ).one()
            print(
                "Postgres connection ok: "
                f"database={row.database}, role={row.role}, server_addr={row.server_addr}"
            )

            ssl_row = (
                await connection.execute(
                    text(
                        """
                        SELECT ssl
                        FROM pg_stat_ssl
                        WHERE pid = pg_backend_pid()
                        """
                    )
                )
            ).one_or_none()
            print(f"Postgres SSL active: {bool(ssl_row and ssl_row.ssl)}")

            if args.check_tables:
                await check_tables(connection)
    finally:
        await engine.dispose()

    return 0


async def check_tables(connection) -> None:
    table_rows = (
        await connection.execute(
            text(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'public'
                """
            )
        )
    ).all()
    tables = {row.table_name for row in table_rows}
    missing = sorted(EXPECTED_TABLES - tables)
    if missing:
        raise RuntimeError(f"Missing expected tables: {', '.join(missing)}")

    revision = (
        await connection.execute(text("SELECT version_num FROM alembic_version"))
    ).scalar_one()
    source_count = (
        await connection.execute(text("SELECT count(*) FROM curated_sources"))
    ).scalar_one()
    if source_count <= 0:
        raise RuntimeError("curated_sources is empty; run python -m app.cli seed_sources")

    print(f"Alembic revision: {revision}")
    print(f"Core tables ok: {', '.join(sorted(EXPECTED_TABLES))}")
    print(f"Curated sources seeded: {source_count}")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

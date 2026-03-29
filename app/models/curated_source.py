"""CuratedSource ORM model.

Each source has metadata for per-source scraping: type, category, tags,
and an optional scraper_config JSON blob with source-specific parsing rules.
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class CuratedSource(Base):
    __tablename__ = "curated_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    url: Mapped[str] = mapped_column(Text, unique=True, nullable=False)

    # ── Source metadata ───────────────────────────────────────
    source_type: Mapped[str] = mapped_column(
        String(20), default="rss", server_default="rss",
        comment="rss | web_scrape | api"
    )
    category: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True,
        comment="Broad category: tech, science, business, etc."
    )
    tags: Mapped[Optional[list]] = mapped_column(
        JSONB, nullable=True, default=list,
        comment="Fine-grained topic tags for heuristic matching."
    )
    description: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True,
        comment="Human-readable description of what this source covers."
    )
    scraper_config: Mapped[Optional[dict]] = mapped_column(
        JSONB, nullable=True, default=dict,
        comment="Source-specific parsing rules (selectors, pagination, etc.)."
    )

    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

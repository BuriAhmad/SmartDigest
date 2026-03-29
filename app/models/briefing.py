"""Briefing ORM model — formerly 'Subscription'.

A Briefing represents a user-configured digest feed: a topic, an intent
statement describing what they want to learn, selected content sources,
and a delivery schedule.
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Briefing(Base):
    __tablename__ = "briefings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=False
    )

    # ── Display & identity ────────────────────────────────────
    topic: Mapped[str] = mapped_column(String(200), nullable=False)

    # ── Structured intent fields ──────────────────────────────
    intent_description: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True,
        comment="What the user wants to learn or track — the core intent."
    )
    keywords: Mapped[Optional[list]] = mapped_column(
        JSONB, nullable=True, default=list,
        comment="Explicit keyword list for heuristic filtering."
    )
    example_articles: Mapped[Optional[list]] = mapped_column(
        JSONB, nullable=True, default=list,
        comment="Optional example article titles/URLs that represent useful content."
    )
    exclusion_keywords: Mapped[Optional[list]] = mapped_column(
        JSONB, nullable=True, default=list,
        comment="Topics/keywords to explicitly exclude from results."
    )

    # ── Source selection ───────────────────────────────────────
    sources: Mapped[list] = mapped_column(JSONB, nullable=False)

    # ── Delivery config ───────────────────────────────────────
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    schedule: Mapped[str] = mapped_column(
        String(50), default="0 7 * * *", server_default="0 7 * * *"
    )
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        Index(
            "idx_briefings_user",
            "user_id",
            postgresql_where=(active == True),  # noqa: E712
        ),
    )

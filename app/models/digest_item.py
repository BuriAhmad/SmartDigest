"""DigestItem ORM model."""

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Float, ForeignKey, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class DigestItem(Base):
    __tablename__ = "digest_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    digest_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("digests.id"), nullable=False
    )
    source_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    title: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    item_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    raw_content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    fetch_duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    published_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # ── Filter / relevance metadata ───────────────────────────
    heuristic_score: Mapped[Optional[float]] = mapped_column(
        Float, nullable=True,
        comment="Score from keyword/heuristic pre-filter (0.0–1.0)."
    )
    llm_relevance_score: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True,
        comment="LLM-assigned relevance to user intent (1–10)."
    )
    llm_relevance_reason: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True,
        comment="One-line LLM explanation of why this article is relevant."
    )

    digest = relationship("Digest", back_populates="items")

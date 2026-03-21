"""PipelineEvent ORM model — the observability backbone."""

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class PipelineEvent(Base):
    __tablename__ = "pipeline_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    digest_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("digests.id"), nullable=True
    )
    stage: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    error_msg: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    item_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        Index("idx_pipeline_events_digest", "digest_id"),
        Index("idx_pipeline_events_created", created_at.desc()),
    )

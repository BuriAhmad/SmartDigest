"""Subscription ORM model."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    api_key_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("api_keys.id"), nullable=False
    )
    topic: Mapped[str] = mapped_column(String(200), nullable=False)
    sources: Mapped[list] = mapped_column(JSONB, nullable=False)
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
            "idx_subscriptions_api_key",
            "api_key_id",
            postgresql_where=(active == True),  # noqa: E712
        ),
    )

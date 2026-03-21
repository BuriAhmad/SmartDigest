"""Pydantic schemas for subscription endpoints."""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, field_validator


# Allowed cron schedules for MVP (expandable in V2)
ALLOWED_SCHEDULES = {
    "0 7 * * *",   # Daily 7 AM UTC
    "0 6 * * *",   # Daily 6 AM UTC
    "0 8 * * *",   # Daily 8 AM UTC
    "0 12 * * *",  # Daily noon UTC
    "0 18 * * *",  # Daily 6 PM UTC
}


class SubscriptionCreate(BaseModel):
    """Request body for creating a subscription."""

    topic: str
    sources: List[str]
    email: str
    schedule: str = "0 7 * * *"

    @field_validator("topic")
    @classmethod
    def topic_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Topic cannot be empty")
        if len(v) > 200:
            raise ValueError("Topic must be 200 characters or less")
        return v

    @field_validator("sources")
    @classmethod
    def sources_not_empty(cls, v: List[str]) -> List[str]:
        if not v:
            raise ValueError("At least one source is required")
        return v

    @field_validator("email")
    @classmethod
    def email_valid(cls, v: str) -> str:
        v = v.strip()
        if not v or "@" not in v:
            raise ValueError("A valid email address is required")
        return v

    @field_validator("schedule")
    @classmethod
    def schedule_allowed(cls, v: str) -> str:
        if v not in ALLOWED_SCHEDULES:
            raise ValueError(
                f"Schedule must be one of: {', '.join(sorted(ALLOWED_SCHEDULES))}"
            )
        return v


class SubscriptionUpdate(BaseModel):
    """Request body for updating a subscription (all fields optional)."""

    topic: Optional[str] = None
    sources: Optional[List[str]] = None
    email: Optional[str] = None
    schedule: Optional[str] = None
    active: Optional[bool] = None

    @field_validator("topic")
    @classmethod
    def topic_not_empty(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            v = v.strip()
            if not v:
                raise ValueError("Topic cannot be empty")
        return v

    @field_validator("sources")
    @classmethod
    def sources_not_empty(cls, v: Optional[List[str]]) -> Optional[List[str]]:
        if v is not None and len(v) == 0:
            raise ValueError("At least one source is required")
        return v

    @field_validator("email")
    @classmethod
    def email_valid(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            v = v.strip()
            if not v or "@" not in v:
                raise ValueError("A valid email address is required")
        return v

    @field_validator("schedule")
    @classmethod
    def schedule_allowed(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in ALLOWED_SCHEDULES:
            raise ValueError(
                f"Schedule must be one of: {', '.join(sorted(ALLOWED_SCHEDULES))}"
            )
        return v


class SubscriptionResponse(BaseModel):
    """Response body for a subscription."""

    id: int
    api_key_id: int
    topic: str
    sources: List[str]
    email: str
    schedule: str
    active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class SourceResponse(BaseModel):
    """Response body for a curated source."""

    id: int
    name: str
    rss_url: str
    active: bool

    model_config = {"from_attributes": True}

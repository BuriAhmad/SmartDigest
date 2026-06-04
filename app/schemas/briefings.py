"""Pydantic schemas for briefing endpoints (formerly 'subscriptions')."""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, field_validator


def validate_daily_schedule(v: str) -> str:
    """Accept daily cron schedules at any 30-minute interval."""
    parts = v.strip().split()
    if len(parts) != 5 or parts[2:] != ["*", "*", "*"]:
        raise ValueError("Schedule must be a daily cron expression")

    try:
        minute = int(parts[0])
        hour = int(parts[1])
    except ValueError as exc:
        raise ValueError("Schedule hour and minute must be numbers") from exc

    if hour < 0 or hour > 23:
        raise ValueError("Schedule hour must be between 0 and 23")
    if minute not in {0, 30}:
        raise ValueError("Schedule minute must be 00 or 30")
    return f"{minute} {hour} * * *"


class BriefingCreate(BaseModel):
    """Request body for creating a briefing."""

    topic: str
    intent_description: str
    keywords: List[str]
    sources: List[str]
    email: str
    schedule: str = "0 7 * * *"
    example_articles: Optional[List[str]] = None
    exclusion_keywords: Optional[List[str]] = None

    @field_validator("topic")
    @classmethod
    def topic_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Topic cannot be empty")
        if len(v) > 200:
            raise ValueError("Topic must be 200 characters or less")
        return v

    @field_validator("intent_description")
    @classmethod
    def intent_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v or len(v) < 20:
            raise ValueError(
                "Intent description must be at least 20 characters. "
                "Describe what you want to learn or track."
            )
        if len(v) > 2000:
            raise ValueError("Intent description must be 2000 characters or less")
        return v

    @field_validator("keywords")
    @classmethod
    def keywords_not_empty(cls, v: List[str]) -> List[str]:
        cleaned = [k.strip() for k in v if k.strip()]
        if not cleaned:
            raise ValueError("At least one keyword is required")
        if len(cleaned) > 20:
            raise ValueError("Maximum 20 keywords allowed")
        return cleaned

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
        return validate_daily_schedule(v)

    @field_validator("exclusion_keywords")
    @classmethod
    def clean_exclusions(cls, v: Optional[List[str]]) -> Optional[List[str]]:
        if v is None:
            return v
        return [k.strip() for k in v if k.strip()]

    @field_validator("example_articles")
    @classmethod
    def clean_examples(cls, v: Optional[List[str]]) -> Optional[List[str]]:
        if v is None:
            return v
        return [a.strip() for a in v if a.strip()]


class BriefingUpdate(BaseModel):
    """Request body for updating a briefing (all fields optional)."""

    topic: Optional[str] = None
    intent_description: Optional[str] = None
    keywords: Optional[List[str]] = None
    sources: Optional[List[str]] = None
    email: Optional[str] = None
    schedule: Optional[str] = None
    active: Optional[bool] = None
    example_articles: Optional[List[str]] = None
    exclusion_keywords: Optional[List[str]] = None

    @field_validator("topic")
    @classmethod
    def topic_not_empty(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            v = v.strip()
            if not v:
                raise ValueError("Topic cannot be empty")
        return v

    @field_validator("intent_description")
    @classmethod
    def intent_not_empty(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            v = v.strip()
            if len(v) < 20:
                raise ValueError("Intent description must be at least 20 characters")
        return v

    @field_validator("keywords")
    @classmethod
    def keywords_not_empty(cls, v: Optional[List[str]]) -> Optional[List[str]]:
        if v is not None:
            cleaned = [k.strip() for k in v if k.strip()]
            if not cleaned:
                raise ValueError("At least one keyword is required")
            return cleaned
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
        if v is not None:
            return validate_daily_schedule(v)
        return v


class BriefingResponse(BaseModel):
    """Response body for a briefing."""

    id: int
    user_id: int
    topic: str
    intent_description: Optional[str] = None
    keywords: Optional[List[str]] = None
    sources: List[str]
    email: str
    schedule: str
    active: bool
    created_at: datetime
    example_articles: Optional[List[str]] = None
    exclusion_keywords: Optional[List[str]] = None

    model_config = {"from_attributes": True}


class SourceResponse(BaseModel):
    """Response body for a curated source."""

    id: int
    name: str
    url: str
    source_type: str = "rss"
    category: Optional[str] = None
    tags: Optional[list] = None
    description: Optional[str] = None
    active: bool

    model_config = {"from_attributes": True}

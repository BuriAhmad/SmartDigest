"""Pydantic schemas for API key endpoints."""

from datetime import datetime

from pydantic import BaseModel


class KeyCreateResponse(BaseModel):
    """Returned once on key creation — plaintext key is never shown again."""

    key: str
    prefix: str
    created_at: datetime

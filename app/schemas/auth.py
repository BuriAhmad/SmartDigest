"""Pydantic schemas for authentication endpoints."""

from datetime import datetime

from pydantic import BaseModel


class UserResponse(BaseModel):
    """Public user info returned by API."""

    id: int
    email: str
    name: str
    created_at: datetime

    model_config = {"from_attributes": True}

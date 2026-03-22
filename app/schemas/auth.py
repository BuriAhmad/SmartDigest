"""Pydantic schemas for authentication endpoints."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, EmailStr


class UserResponse(BaseModel):
    """Public user info returned by API."""

    id: int
    email: str
    name: str
    plan: str
    created_at: datetime

    model_config = {"from_attributes": True}

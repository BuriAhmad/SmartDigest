"""Application configuration via Pydantic BaseSettings."""

import os
from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """All environment variables for SmartDigest.

    Loaded from .env file automatically. Never use os.environ elsewhere.
    """

    DATABASE_URL: str = os.getenv(
        "DATABASE_URL",
        "postgresql+asyncpg://postgres:postgres@localhost:5432/smartdigest",
    )
    
    def __init__(self, **data):
        super().__init__(**data)
        # Convert standard postgres:// URLs from Railway to asyncpg format
        if self.DATABASE_URL.startswith("postgresql://"):
            self.DATABASE_URL = self.DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)
        elif self.DATABASE_URL.startswith("postgres://"):
            self.DATABASE_URL = self.DATABASE_URL.replace("postgres://", "postgresql+asyncpg://", 1)
    REDIS_URL: str = "redis://localhost:6379"
    GEMINI_API_KEY: str = ""
    RESEND_API_KEY: str = ""
    RESEND_FROM_EMAIL: str = "onboarding@resend.dev"
    ENV: str = "development"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    @property
    def is_production(self) -> bool:
        return self.ENV == "production"


@lru_cache
def get_settings() -> Settings:
    """Cached singleton — import and call this everywhere."""
    return Settings()

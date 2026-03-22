"""Application configuration via Pydantic BaseSettings."""

from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """All environment variables for SmartDigest.

    Loaded from .env file or OS environment automatically.
    Railway injects DATABASE_URL, REDIS_URL, etc. as env vars.
    """

    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/smartdigest"
    REDIS_URL: str = "redis://localhost:6379"
    GEMINI_API_KEY: str = ""
    RESEND_API_KEY: str = ""
    RESEND_FROM_EMAIL: str = "onboarding@resend.dev"
    JWT_SECRET: str = "dev-secret-change-in-production-abc123"
    ENV: str = "development"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    @model_validator(mode="after")
    def fix_database_url(self) -> "Settings":
        """Convert standard postgres:// URLs (from Railway) to asyncpg format."""
        if self.DATABASE_URL.startswith("postgresql://"):
            self.DATABASE_URL = self.DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)
        elif self.DATABASE_URL.startswith("postgres://"):
            self.DATABASE_URL = self.DATABASE_URL.replace("postgres://", "postgresql+asyncpg://", 1)
        return self

    @property
    def is_production(self) -> bool:
        return self.ENV == "production"


@lru_cache
def get_settings() -> Settings:
    """Cached singleton — import and call this everywhere."""
    return Settings()

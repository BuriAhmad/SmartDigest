"""Application configuration via Pydantic BaseSettings."""

from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """All environment variables for SmartDigest.

    Loaded from .env file automatically. Never use os.environ elsewhere.
    """

    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/smartdigest"
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

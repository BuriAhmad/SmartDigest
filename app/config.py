"""Application configuration via Pydantic BaseSettings."""

from functools import lru_cache
from typing import Any, Dict

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
    FIREBASE_SERVICE_ACCOUNT_PATH: str = "~/.config/smartdigest/firebase/firebase-admin-service-account.json"
    FIREBASE_SERVICE_ACCOUNT_JSON: str = ""
    FIREBASE_WEB_API_KEY: str = "AIzaSyDqPpFLX9e6ViHhWgeulLece4L034HUrGE"
    FIREBASE_WEB_AUTH_DOMAIN: str = "smartdigest-f50ee.firebaseapp.com"
    FIREBASE_WEB_PROJECT_ID: str = "smartdigest-f50ee"
    FIREBASE_WEB_STORAGE_BUCKET: str = "smartdigest-f50ee.firebasestorage.app"
    FIREBASE_WEB_MESSAGING_SENDER_ID: str = "1001561444383"
    FIREBASE_WEB_APP_ID: str = "1:1001561444383:web:dccd126eb03ed93b53852a"
    FIREBASE_WEB_MEASUREMENT_ID: str = "G-DPBDKNSEQX"
    SEMANTIC_RETRIEVAL_ENABLED: bool = True
    SEMANTIC_WARMUP_ENABLED: bool = False
    SEMANTIC_MODEL_LOCAL_FILES_ONLY: bool = True
    SEMANTIC_MODEL_LOAD_TIMEOUT_SECONDS: float = 5.0
    SEMANTIC_MODEL_NAME: str = "sentence-transformers/all-MiniLM-L6-v2"
    SEMANTIC_TOP_K: int = 20
    SEMANTIC_MIN_SCORE: float = 0.2
    SEMANTIC_QUERY_MAX_CHARS: int = 2000
    SEMANTIC_ARTICLE_MAX_CHARS: int = 1800
    RETRIEVAL_UNION_MAX_K: int = 30
    GEMINI_RELEVANCE_MODELS: str = "gemini-2.5-flash,gemini-2.5-flash-lite"
    GEMINI_SUMMARY_MODELS: str = "gemini-2.5-flash,gemini-2.5-flash-lite"
    GEMINI_REQUEST_TIMEOUT_SECONDS: float = 45.0
    GEMINI_RETRY_ATTEMPTS: int = 2
    GEMINI_RETRY_BACKOFF_SECONDS: float = 1.0
    GEMINI_SUMMARY_BATCH_SIZE: int = 8
    GEMINI_SUMMARY_ARTICLE_MAX_CHARS: int = 1800
    ARQ_JOB_EXPIRES_SECONDS: int = 604800
    QUEUED_DIGEST_RECOVERY_AFTER_MINUTES: int = 5
    ARQ_JOB_TIMEOUT_SECONDS: int = 900
    ARQ_MAX_TRIES: int = 1

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

    @property
    def firebase_web_config(self) -> Dict[str, Any]:
        """Return the public Firebase web config used by the login page."""
        config = {
            "apiKey": self.FIREBASE_WEB_API_KEY,
            "authDomain": self.FIREBASE_WEB_AUTH_DOMAIN,
            "projectId": self.FIREBASE_WEB_PROJECT_ID,
            "storageBucket": self.FIREBASE_WEB_STORAGE_BUCKET,
            "messagingSenderId": self.FIREBASE_WEB_MESSAGING_SENDER_ID,
            "appId": self.FIREBASE_WEB_APP_ID,
        }
        if self.FIREBASE_WEB_MEASUREMENT_ID:
            config["measurementId"] = self.FIREBASE_WEB_MEASUREMENT_ID
        return config


@lru_cache
def get_settings() -> Settings:
    """Cached singleton — import and call this everywhere."""
    return Settings()

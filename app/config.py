"""Application configuration via Pydantic BaseSettings."""

import json
from functools import lru_cache
from typing import Any, Dict, Literal
from urllib.parse import parse_qsl, urlencode, urlparse, urlsplit, urlunsplit

from pydantic import model_validator
from pydantic_settings import BaseSettings


def normalise_database_url(database_url: str) -> str:
    """Convert platform Postgres URLs to SQLAlchemy's asyncpg dialect."""
    if database_url.startswith("postgresql://"):
        return database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if database_url.startswith("postgres://"):
        return database_url.replace("postgres://", "postgresql+asyncpg://", 1)
    return database_url


def prepare_asyncpg_database_url(database_url: str) -> tuple[str, dict[str, Any]]:
    """Return a SQLAlchemy asyncpg URL plus driver connect args.

    asyncpg does not accept libpq-style sslmode query parameters. Neon emits
    `sslmode=require`, so translate that URL flag to asyncpg's native `ssl`.
    """
    normalised_url = normalise_database_url(database_url)
    parsed = urlsplit(normalised_url)
    if not parsed.scheme.endswith("+asyncpg"):
        return normalised_url, {}

    connect_args: dict[str, Any] = {}
    filtered_query: list[tuple[str, str]] = []

    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        if key != "sslmode":
            filtered_query.append((key, value))
            continue

        sslmode = value.lower()
        if sslmode == "disable":
            connect_args["ssl"] = False
        elif sslmode in {"allow", "prefer", "require", "verify-ca", "verify-full"}:
            connect_args["ssl"] = True
        else:
            filtered_query.append((key, value))

    prepared_url = urlunsplit(
        parsed._replace(query=urlencode(filtered_query, doseq=True))
    )
    return prepared_url, connect_args


class Settings(BaseSettings):
    """All environment variables for SmartDigest.

    Loaded from a local .env file or the process environment automatically.
    Google Cloud Run injects production values and Secret Manager references.
    """

    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/smartdigest"
    REDIS_URL: str = "redis://localhost:6379"
    LLM_API_KEY: str = ""
    LLM_RELEVANCE_MODELS: str = ""
    LLM_SUMMARY_MODELS: str = ""
    LLM_REQUEST_TIMEOUT_SECONDS: float = 45.0
    LLM_RETRY_ATTEMPTS: int = 2
    LLM_RETRY_BACKOFF_SECONDS: float = 1.0
    LLM_RELEVANCE_BATCH_SIZE: int = 10
    LLM_RELEVANCE_MAX_OUTPUT_TOKENS: int = 4096
    LLM_SUMMARY_BATCH_SIZE: int = 8
    LLM_SUMMARY_ARTICLE_MAX_CHARS: int = 1800
    GEMINI_API_KEY: str = ""
    RESEND_API_KEY: str = ""
    RESEND_FROM_EMAIL: str = "SmartDigest <digest@smartdigest.app>"
    JWT_SECRET: str = "dev-secret-change-in-production-abc123"
    ENV: str = "development"
    APP_ROLE: Literal["all", "web", "worker", "release"] = "all"
    FIREBASE_SERVICE_ACCOUNT_PATH: str = "~/.config/smartdigest/firebase/firebase-admin-service-account.json"
    FIREBASE_SERVICE_ACCOUNT_JSON: str = ""
    FIREBASE_WEB_API_KEY: str = "AIzaSyDqPpFLX9e6ViHhWgeulLece4L034HUrGE"
    FIREBASE_WEB_AUTH_DOMAIN: str = "smartdigest-f50ee.firebaseapp.com"
    FIREBASE_WEB_PROJECT_ID: str = "smartdigest-f50ee"
    FIREBASE_WEB_STORAGE_BUCKET: str = "smartdigest-f50ee.firebasestorage.app"
    FIREBASE_WEB_MESSAGING_SENDER_ID: str = "1001561444383"
    FIREBASE_WEB_APP_ID: str = "1:1001561444383:web:dccd126eb03ed93b53852a"
    FIREBASE_WEB_MEASUREMENT_ID: str = "G-DPBDKNSEQX"
    HF_HOME: str = "/opt/models/huggingface"
    TRANSFORMERS_CACHE: str = "/opt/models/huggingface/transformers"
    SENTENCE_TRANSFORMERS_HOME: str = "/opt/models/sentence-transformers"
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
    RERANKER_ENABLED: bool = True
    RERANKER_REQUIRED: bool = True
    RERANKER_WARMUP_ENABLED: bool = False
    RERANKER_MODEL_LOCAL_FILES_ONLY: bool = True
    RERANKER_MODEL_LOAD_TIMEOUT_SECONDS: float = 45.0
    RERANKER_MODEL_NAME: str = "cross-encoder/ettin-reranker-68m-v1"
    RERANKER_TOP_K: int = 10
    RERANKER_MIN_KEEP: int = 5
    RERANKER_MIN_SCORE: float | None = None
    RERANKER_MAX_SCORE_DROP: float | None = None
    RERANKER_ARTICLE_MAX_CHARS: int = 1800
    RERANKER_BATCH_SIZE: int = 8
    GEMINI_RELEVANCE_MODELS: str = "gemini-2.5-flash-lite,gemini-2.5-flash"
    GEMINI_SUMMARY_MODELS: str = "gemini-2.5-flash-lite,gemini-2.5-flash"
    GEMINI_REQUEST_TIMEOUT_SECONDS: float = 45.0
    GEMINI_RETRY_ATTEMPTS: int = 2
    GEMINI_RETRY_BACKOFF_SECONDS: float = 1.0
    GEMINI_SUMMARY_BATCH_SIZE: int = 8
    GEMINI_SUMMARY_ARTICLE_MAX_CHARS: int = 1800
    ARQ_JOB_EXPIRES_SECONDS: int = 604800
    QUEUED_DIGEST_RECOVERY_AFTER_MINUTES: int = 5
    PROCESSING_DIGEST_RECOVERY_AFTER_MINUTES: int = 30
    ARQ_JOB_TIMEOUT_SECONDS: int = 900
    ARQ_MAX_TRIES: int = 4
    ARQ_MAX_JOBS: int = 2
    PIPELINE_RETRY_DEFER_SECONDS: int = 300
    ALLOW_INSECURE_PRODUCTION_REDIS: bool = False

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    @model_validator(mode="after")
    def normalise_and_validate_urls(self) -> "Settings":
        """Normalise URLs and reject incomplete production configuration."""
        self.DATABASE_URL = normalise_database_url(self.DATABASE_URL)
        if self.is_production:
            self._validate_production_database_url()
            self._validate_production_model_config()
            if self.APP_ROLE in {"all", "web", "worker"}:
                self._validate_production_redis_url()
            if self.APP_ROLE in {"all", "web"}:
                self._validate_production_web_config()
            if self.APP_ROLE in {"all", "worker"}:
                self._validate_production_worker_config()
        return self

    def _validate_production_database_url(self) -> None:
        parsed = urlparse(self.DATABASE_URL)
        local_hosts = {"localhost", "127.0.0.1", "::1", ""}
        if parsed.scheme != "postgresql+asyncpg":
            raise ValueError("Production DATABASE_URL must use PostgreSQL")
        if parsed.hostname in local_hosts:
            raise ValueError("Production DATABASE_URL must not point at local Postgres")
        if not parsed.username or not parsed.path.strip("/"):
            raise ValueError("Production DATABASE_URL must include a user and database")

    def _validate_production_redis_url(self) -> None:
        parsed = urlparse(self.REDIS_URL)
        local_hosts = {"localhost", "127.0.0.1", "::1", ""}
        if parsed.scheme not in {"redis", "rediss"}:
            raise ValueError("REDIS_URL must use redis:// or rediss://")
        if parsed.hostname in local_hosts:
            raise ValueError("Production REDIS_URL must not point at local Redis")
        if parsed.scheme != "rediss" and not self.ALLOW_INSECURE_PRODUCTION_REDIS:
            raise ValueError(
                "Production REDIS_URL should use rediss:// for TLS; set "
                "ALLOW_INSECURE_PRODUCTION_REDIS=true only for an intentional exception"
            )

    def _validate_production_model_config(self) -> None:
        if self.SEMANTIC_RETRIEVAL_ENABLED and not self.SEMANTIC_MODEL_LOCAL_FILES_ONLY:
            raise ValueError(
                "Production semantic retrieval must use local-only model loading"
            )
        if self.RERANKER_ENABLED and not self.RERANKER_MODEL_LOCAL_FILES_ONLY:
            raise ValueError(
                "Production reranker must use local-only model loading"
            )

    def _validate_production_web_config(self) -> None:
        if (
            len(self.JWT_SECRET) < 32
            or self.JWT_SECRET == "dev-secret-change-in-production-abc123"
        ):
            raise ValueError("Production web requires a strong JWT_SECRET")

        if not self.FIREBASE_SERVICE_ACCOUNT_JSON:
            raise ValueError(
                "Production web requires FIREBASE_SERVICE_ACCOUNT_JSON"
            )
        try:
            service_account = json.loads(self.FIREBASE_SERVICE_ACCOUNT_JSON)
        except json.JSONDecodeError as exc:
            raise ValueError(
                "FIREBASE_SERVICE_ACCOUNT_JSON must contain valid JSON"
            ) from exc

        required_fields = {"project_id", "private_key", "client_email"}
        if not isinstance(service_account, dict) or not all(
            service_account.get(field) for field in required_fields
        ):
            raise ValueError(
                "FIREBASE_SERVICE_ACCOUNT_JSON is missing required fields"
            )

    def _validate_production_worker_config(self) -> None:
        if not (self.LLM_API_KEY or self.GEMINI_API_KEY):
            raise ValueError("Production worker requires LLM_API_KEY")
        if not self.RESEND_API_KEY:
            raise ValueError("Production worker requires RESEND_API_KEY")
        if not self.RESEND_FROM_EMAIL.strip():
            raise ValueError("Production worker requires RESEND_FROM_EMAIL")
        if self.ARQ_MAX_JOBS < 1:
            raise ValueError("ARQ_MAX_JOBS must be at least 1")

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

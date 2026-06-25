"""Google GenAI adapter with model fallback and JSON response handling."""

import asyncio
import json
from dataclasses import dataclass
from typing import Any, Optional, Sequence

import structlog

from app.config import get_settings

logger = structlog.get_logger()

DEFAULT_MODELS = ["gemini-2.5-flash-lite", "gemini-2.5-flash"]
RETRYABLE_STATUS_CODES = {429, 500, 503, 504}


class LLMGenerationError(RuntimeError):
    """Raised when every configured LLM model fails."""


class LLMConfigurationError(LLMGenerationError):
    """Raised when the LLM provider cannot be configured."""


class LLMRetryableError(LLMGenerationError):
    """Raised when the provider failure is likely to succeed on a later retry."""


@dataclass(frozen=True)
class LLMGenerationConfig:
    """Runtime options for a structured generation call."""

    models: Sequence[str]
    temperature: float
    max_output_tokens: int
    timeout_seconds: float
    retry_attempts: int
    retry_backoff_seconds: float
    log_name: str


def get_llm_api_key(settings: Optional[Any] = None) -> str:
    """Return the configured LLM API key, accepting legacy Gemini env names."""
    settings = settings or get_settings()
    return str(
        _setting(settings, "LLM_API_KEY", "")
        or _setting(settings, "GEMINI_API_KEY", "")
        or ""
    )


def configured_models(raw_models: str, default: Sequence[str] = DEFAULT_MODELS) -> list[str]:
    """Parse a comma-separated model fallback chain."""
    models = [model.strip() for model in (raw_models or "").split(",") if model.strip()]
    return models or list(default)


async def generate_json(
    prompt: str,
    response_schema: Any,
    config: LLMGenerationConfig,
    api_key: Optional[str] = None,
) -> Any:
    """Generate structured JSON using Google GenAI and configured fallbacks."""
    api_key = api_key or get_llm_api_key()
    if not api_key:
        raise LLMConfigurationError("LLM API key is not configured")

    genai, types = _import_google_genai()
    last_error = "All models failed"
    last_retryable = False

    async with genai.Client(api_key=api_key).aio as aclient:
        for model in config.models:
            attempts = max(1, config.retry_attempts)
            for attempt in range(1, attempts + 1):
                try:
                    logger.info(
                        "llm.generate_json.trying_model",
                        call=config.log_name,
                        model=model,
                        attempt=attempt,
                    )
                    request = aclient.models.generate_content(
                        model=model,
                        contents=prompt,
                        config=types.GenerateContentConfig(
                            temperature=config.temperature,
                            max_output_tokens=config.max_output_tokens,
                            response_mime_type="application/json",
                            response_schema=response_schema,
                        ),
                    )
                    response = await asyncio.wait_for(
                        request,
                        timeout=config.timeout_seconds,
                    )
                    return _structured_payload(response)

                except Exception as exc:
                    status_code = _status_code(exc)
                    message = _error_message(exc)
                    last_error = f"{exc.__class__.__name__} on {model}: {message[:160]}"
                    last_retryable = _is_retryable_llm_error(exc, status_code)
                    logger.warning(
                        "llm.generate_json.error",
                        call=config.log_name,
                        model=model,
                        attempt=attempt,
                        status_code=status_code,
                        error=message[:200],
                    )

                    if status_code == 429:
                        break
                    if status_code in RETRYABLE_STATUS_CODES and attempt < attempts:
                        await asyncio.sleep(config.retry_backoff_seconds * attempt)
                        continue
                    if status_code is None and attempt < attempts:
                        await asyncio.sleep(config.retry_backoff_seconds * attempt)
                        continue
                    break

    error_message = f"{config.log_name} failed: {last_error}"
    if last_retryable:
        raise LLMRetryableError(error_message)
    raise LLMGenerationError(error_message)


def _structured_payload(response: Any) -> Any:
    parsed = getattr(response, "parsed", None)
    if parsed is not None:
        return parsed

    text = getattr(response, "text", "") or ""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        cleaned = "\n".join(lines[1:-1]) if len(lines) > 2 else cleaned

    start = cleaned.find("[")
    end = cleaned.rfind("]")
    if start == -1 or end == -1:
        raise LLMGenerationError("No JSON array found in LLM response")

    try:
        return json.loads(cleaned[start:end + 1])
    except json.JSONDecodeError as exc:
        raise LLMGenerationError(f"Invalid JSON from LLM: {exc}") from exc


def _import_google_genai() -> tuple[Any, Any]:
    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:
        raise LLMConfigurationError(
            "google-genai is not installed; install project requirements"
        ) from exc
    return genai, types


def _setting(settings: Any, name: str, default: Any) -> Any:
    return getattr(settings, name, default)


def _status_code(exc: Exception) -> Optional[int]:
    for attr in ("status_code", "code"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value
    response = getattr(exc, "response", None)
    value = getattr(response, "status_code", None)
    return value if isinstance(value, int) else None


def _is_retryable_llm_error(exc: Exception, status_code: Optional[int]) -> bool:
    if status_code in RETRYABLE_STATUS_CODES:
        return True
    if isinstance(exc, LLMGenerationError) and not isinstance(exc, LLMConfigurationError):
        return True
    return False


def _error_message(exc: Exception) -> str:
    return str(exc) or exc.__class__.__name__

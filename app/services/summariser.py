"""SummariserService — generates intent-aware article summaries using Google Gemini.

Each article is summarised in the context of the user's specific intent.
The LLM acts as a "knowledgeable advisor" — extracting what matters to the
user and explaining why, not just compressing the article.

Uses fallback model selection and batched API calls for efficiency.
"""

import asyncio
import json
import traceback
from typing import Dict, List, Optional

import httpx
import structlog

from app.config import get_settings

logger = structlog.get_logger()

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
DEFAULT_MODELS = ["gemini-2.5-flash", "gemini-2.5-flash-lite"]
RETRYABLE_STATUS_CODES = {500, 503, 504}


def _build_batch_prompt(
    articles: List[Dict],
    intent_context: str,
    topic: str,
    article_content_max_chars: int,
) -> str:
    """Build an intent-aware summarisation prompt.

    The prompt instructs the LLM to act as a knowledgeable advisor who has read
    each article with the user's specific goal in mind.
    """
    article_blocks = []
    for i, article in enumerate(articles, 1):
        content = article.get("raw_content", "")
        if not content:
            content = article.get("title", "No content available")
        if article_content_max_chars > 0 and len(content) > article_content_max_chars:
            content = content[:article_content_max_chars] + "..."

        # Include the LLM relevance reason if available (from filter stage)
        relevance_note = ""
        if article.get("llm_relevance_reason"):
            relevance_note = f"\nRelevance note: {article['llm_relevance_reason']}"

        source_domain = (article.get("source_url") or "").replace("https://", "").replace("http://", "").split("/")[0]

        article_blocks.append(
            f"[ARTICLE {i}]\n"
            f"Title: {article.get('title', 'Untitled')}\n"
            f"Source: {source_domain}\n"
            f"URL: {article.get('url', '')}\n"
            f"Content: {content}\n"
            f"{relevance_note}"
        )

    articles_text = "\n---\n".join(article_blocks)

    return f"""You are a knowledgeable advisor for someone tracking a specific topic. You have read the articles below with their specific goal in mind. Your job is NOT to generically summarise each article — instead, you must extract what matters to THIS user and explain why.

--- USER'S BRIEFING CONTEXT ---
{intent_context}
--- END CONTEXT ---

For each of the {len(articles)} articles below:
1. Identify what is genuinely relevant to the user's stated intent.
2. Write a 2-4 sentence summary that explains WHAT matters and WHY it matters to the user's specific goal.
3. If an article does NOT meaningfully apply to the user's intent despite appearing topically related, mark it for exclusion.

Write as if you're briefing a colleague: direct, insightful, no filler. Use phrases like "This matters because...", "Key takeaway for your focus on X...", "This signals that...".

IMPORTANT: Respond with ONLY a valid JSON array. Each element must have exactly these fields:
- "index": the article number (1-based integer)
- "summary": your 2-4 sentence advisory summary string
- "exclude": boolean — true if this article should be excluded from the digest (not meaningfully relevant)

Example response format:
[
  {{"index": 1, "summary": "A new framework for...", "exclude": false}},
  {{"index": 2, "summary": "", "exclude": true}}
]

Do NOT include any text before or after the JSON array.

ARTICLES:
{articles_text}"""


async def summarise_articles(
    articles: List[Dict],
    topic: str,
    intent_context: Optional[str] = None,
) -> List[Dict]:
    """Summarise articles with intent-aware Gemini prompting.

    Args:
        articles: List of article dicts with title, url, raw_content.
        topic: The briefing topic label.
        intent_context: Rich intent context string (from build_intent_context).

    Returns:
        The articles list with 'summary' populated and excluded articles removed.
    """
    settings = get_settings()
    log = logger.bind(topic=topic, article_count=len(articles))

    if not articles:
        return articles

    if not settings.GEMINI_API_KEY:
        log.warning("summariser.no_api_key")
        for article in articles:
            article["summary"] = "[Summary unavailable — API key not configured]"
        return articles

    if not intent_context:
        intent_context = f"Topic: {topic}"

    batch_size = max(1, settings.GEMINI_SUMMARY_BATCH_SIZE)
    included_articles: List[Dict] = []
    for start in range(0, len(articles), batch_size):
        batch = articles[start:start + batch_size]
        log.info(
            "summariser.batch_start",
            batch_start=start,
            batch_size=len(batch),
            total=len(articles),
        )
        included_articles.extend(
            await _summarise_batch(batch, topic, intent_context, settings)
        )

    return included_articles


async def _summarise_batch(
    articles: List[Dict],
    topic: str,
    intent_context: str,
    settings,
) -> List[Dict]:
    log = logger.bind(topic=topic, article_count=len(articles))
    prompt = _build_batch_prompt(
        articles,
        intent_context,
        topic,
        settings.GEMINI_SUMMARY_ARTICLE_MAX_CHARS,
    )
    log.info("summariser.calling_gemini", prompt_length=len(prompt))

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.3,
            "maxOutputTokens": 4096,
            "responseMimeType": "application/json",
        },
    }

    last_error = "All models failed"
    for model in _configured_models(settings.GEMINI_SUMMARY_MODELS):
        url = GEMINI_BASE_URL.format(model=model)
        log.info("summariser.trying_model", model=model)
        attempts = max(1, settings.GEMINI_RETRY_ATTEMPTS)
        for attempt in range(1, attempts + 1):
            try:
                timeout = httpx.Timeout(
                    settings.GEMINI_REQUEST_TIMEOUT_SECONDS,
                    connect=10.0,
                )
                async with httpx.AsyncClient(timeout=timeout) as client:
                    resp = await client.post(
                        url,
                        params={"key": settings.GEMINI_API_KEY},
                        json=payload,
                    )

                log.info(
                    "summariser.response_received",
                    model=model,
                    status=resp.status_code,
                    attempt=attempt,
                )

                if resp.status_code == 429:
                    msg = _error_message(resp)
                    log.warning("summariser.quota_exceeded", model=model, message=msg[:120])
                    last_error = f"HTTP 429 on {model}: {msg[:120]}"
                    break

                if resp.status_code != 200:
                    msg = _error_message(resp)
                    log.error(
                        "summariser.api_error",
                        model=model,
                        status=resp.status_code,
                        attempt=attempt,
                        message=msg[:120],
                    )
                    last_error = f"HTTP {resp.status_code} on {model}: {msg[:120]}"
                    if resp.status_code in RETRYABLE_STATUS_CODES and attempt < attempts:
                        await asyncio.sleep(settings.GEMINI_RETRY_BACKOFF_SECONDS * attempt)
                        continue
                    break

                data = resp.json()

                if "error" in data:
                    log.error("summariser.gemini_error_in_200", model=model, error=data["error"])
                    last_error = data["error"].get("message", "unknown")
                    break

                try:
                    candidates = data.get("candidates", [])
                    if not candidates:
                        last_error = f"No candidates from {model}"
                        break

                    raw_text = candidates[0]["content"]["parts"][0]["text"]
                    log.info("summariser.raw_text_preview", preview=raw_text[:200])

                except (KeyError, IndexError) as exc:
                    last_error = str(exc)
                    break

                summaries = _parse_summaries(raw_text, len(articles))
                if not summaries:
                    last_error = f"No parseable summaries from {model}"
                    break

                included_articles = []
                for article_idx, article in enumerate(articles):
                    article_num = article_idx + 1
                    summary_data = summaries.get(article_num, {})

                    if isinstance(summary_data, dict):
                        if summary_data.get("exclude", False):
                            log.info(
                                "summariser.excluded_article",
                                title=article.get("title", "")[:60],
                            )
                            continue
                        article["summary"] = summary_data.get("summary", "[Summary unavailable]")
                    else:
                        article["summary"] = str(summary_data) if summary_data else "[Summary unavailable]"

                    included_articles.append(article)

                log.info(
                    "summariser.complete",
                    model=model,
                    input=len(articles),
                    included=len(included_articles),
                    excluded=len(articles) - len(included_articles),
                )
                return included_articles

            except (httpx.TimeoutException, httpx.TransportError) as exc:
                tb = traceback.format_exc()
                log.error(
                    "summariser.transport_error",
                    model=model,
                    attempt=attempt,
                    error=repr(exc),
                    traceback=tb,
                )
                last_error = f"{exc.__class__.__name__} on {model}"
                if attempt < attempts:
                    await asyncio.sleep(settings.GEMINI_RETRY_BACKOFF_SECONDS * attempt)
                    continue
                break
            except Exception as exc:
                tb = traceback.format_exc()
                log.error("summariser.exception", model=model, error=repr(exc), traceback=tb)
                last_error = f"{exc.__class__.__name__}: {exc}"
                break

    # All models exhausted
    log.error("summariser.all_models_failed", last_error=last_error)
    raise RuntimeError(f"Summarisation failed: {last_error}")


def _parse_summaries(text: str, expected_count: int) -> Dict[int, dict]:
    """Parse Gemini's JSON response into {index: {summary, exclude}}.

    Handles both new format (with exclude field) and legacy format (summary only).
    """
    cleaned = text.strip()

    # Remove markdown code blocks
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        cleaned = "\n".join(lines[1:-1]) if len(lines) > 2 else cleaned

    start = cleaned.find("[")
    end = cleaned.rfind("]")

    if start == -1 or end == -1:
        logger.warning("summariser.no_json_array", response_preview=cleaned[:200])
        return {}

    json_str = cleaned[start:end + 1]

    try:
        parsed = json.loads(json_str)
        if not isinstance(parsed, list):
            return {}

        result = {}
        for item in parsed:
            if isinstance(item, dict):
                idx = item.get("index")
                summary = item.get("summary", "")
                exclude = item.get("exclude", False)
                if idx is not None:
                    result[int(idx)] = {
                        "summary": str(summary),
                        "exclude": bool(exclude),
                    }

        return result

    except json.JSONDecodeError as exc:
        logger.warning("summariser.json_parse_error", error=str(exc))
        return {}


def _configured_models(raw_models: str) -> List[str]:
    models = [model.strip() for model in (raw_models or "").split(",") if model.strip()]
    return models or DEFAULT_MODELS


def _error_message(resp: httpx.Response) -> str:
    try:
        data = resp.json()
    except ValueError:
        return resp.text[:300]
    error = data.get("error") if isinstance(data, dict) else None
    if isinstance(error, dict):
        return str(error.get("message") or error)
    return str(data)[:300]

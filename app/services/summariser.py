"""SummariserService: generates intent-aware article summaries.

Each article is summarised in the context of the user's specific intent.
The LLM acts as a knowledgeable advisor, extracting what matters to the user
and explaining why instead of just compressing the article.
"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field
import structlog

from app.config import get_settings
from app.services.llm import (
    LLMGenerationConfig,
    configured_models,
    generate_json,
    get_llm_api_key,
)

logger = structlog.get_logger()

DEFAULT_MODELS = ["gemini-2.5-flash-lite", "gemini-2.5-flash"]


class SummaryResult(BaseModel):
    index: int
    summary: str = Field(min_length=1)


def _build_batch_prompt(
    articles: List[Dict],
    intent_context: str,
    topic: str,
    article_content_max_chars: int,
) -> str:
    """Build an intent-aware summarisation prompt."""
    article_blocks = []
    for i, article in enumerate(articles, 1):
        content = article.get("raw_content", "")
        if not content:
            content = article.get("title", "No content available")
        if article_content_max_chars > 0 and len(content) > article_content_max_chars:
            content = content[:article_content_max_chars] + "..."

        relevance_note = ""
        if article.get("llm_relevance_reason"):
            relevance_note = f"\nRelevance note: {article['llm_relevance_reason']}"

        source_domain = (
            (article.get("source_url") or "")
            .replace("https://", "")
            .replace("http://", "")
            .split("/")[0]
        )

        article_blocks.append(
            f"[ARTICLE {i}]\n"
            f"Title: {article.get('title', 'Untitled')}\n"
            f"Source: {source_domain}\n"
            f"URL: {article.get('url', '')}\n"
            f"Content: {content}\n"
            f"{relevance_note}"
        )

    articles_text = "\n---\n".join(article_blocks)

    return f"""You are a knowledgeable advisor for someone tracking a specific topic. You have read the articles below with their specific goal in mind. Your job is NOT to generically summarise each article - instead, you must extract what matters to THIS user and explain why.

Every article below has already passed the final relevance gate. You are not a selection or filtering stage. Do not reject, exclude, rank, or omit any article. Return exactly one summary for every supplied article index.

--- USER'S BRIEFING CONTEXT ---
{intent_context}
--- END CONTEXT ---

For each of the {len(articles)} articles below:
1. Identify what is genuinely relevant to the user's stated intent.
2. Write a 2-4 sentence summary that explains WHAT matters and WHY it matters to the user's specific goal.

Write as if you're briefing a colleague: direct, insightful, no filler. Use phrases like "This matters because...", "Key takeaway for your focus on X...", "This signals that...".

IMPORTANT: Respond with ONLY a valid JSON array. Each element must have exactly these fields:
- "index": the article number (1-based integer)
- "summary": your 2-4 sentence advisory summary string

Example response format:
[
  {{"index": 1, "summary": "A new framework for..."}},
  {{"index": 2, "summary": "The article reports..."}}
]

Return exactly one element for every article index from 1 through {len(articles)}. Do not include empty summaries.

Do NOT include any text before or after the JSON array.

ARTICLES:
{articles_text}"""


async def summarise_articles(
    articles: List[Dict],
    topic: str,
    intent_context: Optional[str] = None,
) -> List[Dict]:
    """Summarise articles with intent-aware prompting."""
    settings = get_settings()
    log = logger.bind(topic=topic, article_count=len(articles))

    if not articles:
        return articles

    if not get_llm_api_key(settings):
        log.warning("summariser.no_api_key")
        for article in articles:
            article["summary"] = "[Summary unavailable - API key not configured]"
        return articles

    if not intent_context:
        intent_context = f"Topic: {topic}"

    batch_size = max(
        1,
        int(
            _setting(settings, "LLM_SUMMARY_BATCH_SIZE", 0)
            or _setting(settings, "GEMINI_SUMMARY_BATCH_SIZE", 8)
        ),
    )
    summarised_articles: List[Dict] = []
    for start in range(0, len(articles), batch_size):
        batch = articles[start:start + batch_size]
        log.info(
            "summariser.batch_start",
            batch_start=start,
            batch_size=len(batch),
            total=len(articles),
        )
        summarised_articles.extend(
            await _summarise_batch(batch, topic, intent_context, settings)
        )

    return summarised_articles


async def _summarise_batch(
    articles: List[Dict],
    topic: str,
    intent_context: str,
    settings: Any,
) -> List[Dict]:
    log = logger.bind(topic=topic, article_count=len(articles))
    prompt = _build_batch_prompt(
        articles,
        intent_context,
        topic,
        int(
            _setting(settings, "LLM_SUMMARY_ARTICLE_MAX_CHARS", 0)
            or _setting(settings, "GEMINI_SUMMARY_ARTICLE_MAX_CHARS", 1800)
        ),
    )
    log.info("summariser.calling_llm", prompt_length=len(prompt))

    response = await generate_json(
        prompt=prompt,
        response_schema=list[SummaryResult],
        config=LLMGenerationConfig(
            models=configured_models(
                _setting(settings, "LLM_SUMMARY_MODELS", "")
                or _setting(settings, "GEMINI_SUMMARY_MODELS", ""),
                DEFAULT_MODELS,
            ),
            temperature=0.3,
            max_output_tokens=4096,
            timeout_seconds=float(
                _setting(settings, "LLM_REQUEST_TIMEOUT_SECONDS", 0)
                or _setting(settings, "GEMINI_REQUEST_TIMEOUT_SECONDS", 45.0)
            ),
            retry_attempts=int(
                _setting(settings, "LLM_RETRY_ATTEMPTS", 0)
                or _setting(settings, "GEMINI_RETRY_ATTEMPTS", 1)
            ),
            retry_backoff_seconds=float(
                _setting(settings, "LLM_RETRY_BACKOFF_SECONDS", 0)
                or _setting(settings, "GEMINI_RETRY_BACKOFF_SECONDS", 1.0)
            ),
            log_name="summariser",
        ),
    )
    summaries = _normalise_summaries(response, len(articles))
    if not summaries:
        raise RuntimeError("Summarisation failed: no parseable summaries")

    expected_indexes = set(range(1, len(articles) + 1))
    missing_indexes = sorted(expected_indexes - set(summaries))
    if missing_indexes:
        raise RuntimeError(
            "Summarisation failed: missing summaries for article "
            f"indexes {missing_indexes}"
        )

    summarised_articles = []
    for article_idx, article in enumerate(articles):
        article_num = article_idx + 1
        article["summary"] = summaries[article_num]["summary"]
        summarised_articles.append(article)

    log.info(
        "summariser.complete",
        input=len(articles),
        output=len(summarised_articles),
    )
    return summarised_articles


def _normalise_summaries(response: Any, expected_count: int) -> Dict[int, dict]:
    """Normalise SDK parsed objects or JSON dicts into summary records."""
    if not isinstance(response, list):
        return {}

    result: Dict[int, dict] = {}
    for item in response:
        data = item.model_dump() if isinstance(item, BaseModel) else item
        if not isinstance(data, dict):
            continue
        idx = data.get("index")
        if idx is None:
            continue
        try:
            idx_int = int(idx)
            if idx_int < 1 or idx_int > expected_count:
                continue
        except (TypeError, ValueError):
            continue
        summary = str(data.get("summary", "")).strip()
        if not summary:
            continue
        result[idx_int] = {"summary": summary}
    return result


def _setting(settings: Any, name: str, default: Any) -> Any:
    return getattr(settings, name, default)

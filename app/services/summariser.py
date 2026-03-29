"""SummariserService — generates intent-aware article summaries using Google Gemini.

Each article is summarised in the context of the user's specific intent.
The LLM acts as a "knowledgeable advisor" — extracting what matters to the
user and explaining why, not just compressing the article.

Uses fallback model selection and batched API calls for efficiency.
"""

import json
import traceback
from typing import Dict, List, Optional

import httpx
import structlog

from app.config import get_settings

logger = structlog.get_logger()

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
FETCH_TIMEOUT = 60.0

FALLBACK_MODELS = [
    "gemini-2.0-flash",
    "gemini-2.5-flash",
    "gemini-2.0-flash-lite",
    "gemini-2.5-flash-lite",
]


def _build_batch_prompt(
    articles: List[Dict],
    intent_context: str,
    topic: str,
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

    # Build the intent-aware prompt
    if not intent_context:
        intent_context = f"Topic: {topic}"

    prompt = _build_batch_prompt(articles, intent_context, topic)
    log.info("summariser.calling_gemini", prompt_length=len(prompt))

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.3,
            "maxOutputTokens": 4096,
        },
    }

    last_error = "All models failed"
    for model in FALLBACK_MODELS:
        url = GEMINI_BASE_URL.format(model=model)
        log.info("summariser.trying_model", model=model)
        try:
            async with httpx.AsyncClient(timeout=FETCH_TIMEOUT) as client:
                resp = await client.post(
                    url,
                    params={"key": settings.GEMINI_API_KEY},
                    json=payload,
                )

            log.info("summariser.response_received", model=model, status=resp.status_code)

            if resp.status_code == 429:
                body = resp.json()
                msg = body.get("error", {}).get("message", "quota exceeded")
                log.warning("summariser.quota_exceeded", model=model, message=msg[:120])
                last_error = f"429 on {model}: {msg[:80]}"
                continue

            if resp.status_code != 200:
                log.error("summariser.api_error", model=model, status=resp.status_code)
                last_error = f"HTTP {resp.status_code} on {model}"
                continue

            data = resp.json()

            if "error" in data:
                log.error("summariser.gemini_error_in_200", model=model, error=data["error"])
                last_error = data["error"].get("message", "unknown")
                continue

            try:
                candidates = data.get("candidates", [])
                if not candidates:
                    last_error = f"No candidates from {model}"
                    continue

                raw_text = candidates[0]["content"]["parts"][0]["text"]
                log.info("summariser.raw_text_preview", preview=raw_text[:200])

            except (KeyError, IndexError) as exc:
                last_error = str(exc)
                continue

            # Parse the JSON array of summaries
            summaries = _parse_summaries(raw_text, len(articles))

            # Apply summaries and handle exclusions
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
                    # Backward compat: plain string summaries
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

        except Exception as exc:
            tb = traceback.format_exc()
            log.error("summariser.exception", model=model, error=str(exc), traceback=tb)
            last_error = str(exc)
            continue

    # All models exhausted
    log.error("summariser.all_models_failed", last_error=last_error)
    for article in articles:
        if not article.get("summary"):
            article["summary"] = f"[Summary unavailable — {last_error[:80]}]"
    return articles


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

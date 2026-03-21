"""SummariserService — generates article summaries using Google Gemini.

Calls the Gemini REST API directly via httpx — no SDK dependency.
Batches ALL articles into a SINGLE API call to minimize free-tier usage.

Uses fallback model selection: tries gemini-2.0-flash first, falls back to
gemini-2.5-flash if quota exhausted (each has separate free-tier bucket).
"""

import asyncio
import json
from typing import Dict, List

import httpx
import structlog

from app.config import get_settings

logger = structlog.get_logger()

# Gemini REST base URL — model name is substituted per attempt
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
FETCH_TIMEOUT = 60.0  # Gemini can be slow on first call

# Models tried in order — first one that isn't rate-limited wins.
# Each model has its own independent free-tier quota bucket.
FALLBACK_MODELS = [
    "gemini-2.0-flash",
    "gemini-2.5-flash",
    "gemini-2.0-flash-lite",
    "gemini-2.5-flash-lite",
]


def _build_batch_prompt(articles: List[Dict], topic: str) -> str:
    """Build a single prompt that asks Gemini to summarise all articles at once.

    This maximizes context window usage and minimises API calls.
    """
    article_blocks = []
    for i, article in enumerate(articles, 1):
        content = article.get("raw_content", "")
        if not content:
            content = article.get("title", "No content available")
        article_blocks.append(
            f"[ARTICLE {i}]\n"
            f"Title: {article.get('title', 'Untitled')}\n"
            f"URL: {article.get('url', '')}\n"
            f"Content: {content}\n"
        )

    articles_text = "\n---\n".join(article_blocks)

    return f"""You are SmartDigest, an AI content summariser for the topic "{topic}".
Below are {len(articles)} articles. For EACH article, provide a concise 2-3 sentence summary
highlighting the key points relevant to "{topic}".

IMPORTANT: Respond with ONLY a valid JSON array. Each element must have exactly these fields:
- "index": the article number (1-based integer)
- "summary": your 2-3 sentence summary string

Example response format:
[
  {{"index": 1, "summary": "Summary of article 1..."}},
  {{"index": 2, "summary": "Summary of article 2..."}}
]

Do NOT include any text before or after the JSON array. Do NOT use markdown code blocks.

ARTICLES:
{articles_text}"""


async def summarise_articles(
    articles: List[Dict],
    topic: str,
) -> List[Dict]:
    """Summarise a list of articles using Gemini in a single batch call.

    Args:
        articles: List of article dicts with title, url, raw_content.
        topic: The subscription topic for context.

    Returns:
        The same articles list with 'summary' field populated.
    """
    settings = get_settings()
    log = logger.bind(topic=topic, article_count=len(articles))

    if not articles:
        return articles

    if not settings.GEMINI_API_KEY:
        log.warning("summariser.no_api_key", msg="GEMINI_API_KEY not set, skipping summaries")
        for article in articles:
            article["summary"] = "[Summary unavailable — API key not configured]"
        return articles

    prompt = _build_batch_prompt(articles, topic)
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
                # Quota exhausted for this model — try the next one
                body = resp.json()
                msg = body.get("error", {}).get("message", "quota exceeded")
                log.warning("summariser.quota_exceeded", model=model, message=msg[:120])
                last_error = f"429 on {model}: {msg[:80]}"
                continue  # try next model

            if resp.status_code != 200:
                log.error(
                    "summariser.api_error",
                    model=model,
                    status=resp.status_code,
                    full_body=resp.text,
                )
                last_error = f"HTTP {resp.status_code} on {model}"
                continue  # try next model

            data = resp.json()
            log.info("summariser.response_keys", model=model, keys=list(data.keys()))

            if "error" in data:
                log.error("summariser.gemini_error_in_200", model=model, error=data["error"])
                last_error = data["error"].get("message", "unknown")
                continue

            try:
                candidates = data.get("candidates", [])
                log.info("summariser.candidates", model=model, count=len(candidates))
                if not candidates:
                    log.error("summariser.no_candidates", model=model, full_response=data)
                    last_error = f"No candidates from {model}"
                    continue

                first = candidates[0]
                log.info(
                    "summariser.candidate",
                    model=model,
                    finish_reason=first.get("finishReason"),
                )
                raw_text = first["content"]["parts"][0]["text"]
                log.info("summariser.raw_text_preview", preview=raw_text[:200])

            except (KeyError, IndexError) as exc:
                log.error(
                    "summariser.unexpected_shape",
                    model=model,
                    error=str(exc),
                    full_response=data,
                )
                last_error = str(exc)
                continue

            # Parse the JSON array of summaries
            summaries = _parse_summaries(raw_text, len(articles))
            log.info(
                "summariser.parse_result",
                model=model,
                summaries_found=len(summaries),
                expected=len(articles),
            )

            for article_idx, article in enumerate(articles):
                article_num = article_idx + 1
                article["summary"] = summaries.get(article_num, "[Summary unavailable]")

            log.info("summariser.complete", model=model, summaries_generated=len(summaries))
            return articles  # SUCCESS — stop trying more models

        except Exception as exc:
            import traceback
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


def _parse_summaries(text: str, expected_count: int) -> Dict[int, str]:
    """Parse Gemini's JSON response into a dict of {index: summary}.

    Handles various response quirks: markdown code blocks, extra text, etc.
    """
    # Clean up common Gemini response wrapping
    cleaned = text.strip()

    # Remove markdown code block if present
    if cleaned.startswith("```"):
        # Remove first line (```json or ```) and last line (```)
        lines = cleaned.split("\n")
        cleaned = "\n".join(lines[1:-1]) if len(lines) > 2 else cleaned

    # Try to find JSON array in the response
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
                if idx is not None and summary:
                    result[int(idx)] = str(summary)

        return result

    except json.JSONDecodeError as exc:
        logger.warning("summariser.json_parse_error", error=str(exc), response_preview=json_str[:200])
        return {}

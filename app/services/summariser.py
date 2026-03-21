"""SummariserService — generates article summaries using Google Gemini.

Batches ALL articles into a SINGLE API call to minimize free-tier usage.
Uses gemini-2.0-flash for fast, efficient summarisation.
"""

import asyncio
import json
from typing import Dict, List, Optional

import google.generativeai as genai
import structlog

from app.config import get_settings

logger = structlog.get_logger()

# Gemini model to use
MODEL_NAME = "gemini-2.0-flash"

# Safety settings — be permissive for news content
SAFETY_SETTINGS = [
    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
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

    # Configure the SDK
    genai.configure(api_key=settings.GEMINI_API_KEY)

    prompt = _build_batch_prompt(articles, topic)
    log.info("summariser.calling_gemini", prompt_length=len(prompt))

    try:
        model = genai.GenerativeModel(
            MODEL_NAME,
            generation_config={
                "temperature": 0.3,
                "max_output_tokens": 4096,
            },
            safety_settings=SAFETY_SETTINGS,
        )

        # Run the synchronous SDK call in a thread to avoid blocking
        response = await asyncio.to_thread(
            model.generate_content, prompt
        )

        if not response or not response.text:
            log.warning("summariser.empty_response")
            for article in articles:
                article["summary"] = "[Summary unavailable]"
            return articles

        # Parse the JSON response
        summaries = _parse_summaries(response.text, len(articles))

        for article_idx, article in enumerate(articles):
            article_num = article_idx + 1
            if article_num in summaries:
                article["summary"] = summaries[article_num]
            else:
                article["summary"] = "[Summary unavailable]"

        log.info(
            "summariser.complete",
            summaries_generated=len(summaries),
        )

    except Exception as exc:
        log.error("summariser.error", error=str(exc))
        for article in articles:
            if "summary" not in article or not article.get("summary"):
                article["summary"] = "[Summary unavailable]"

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

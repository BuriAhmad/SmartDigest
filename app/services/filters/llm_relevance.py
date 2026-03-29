"""LLM relevance filter — Gemini-based scoring of article relevance to user intent.

Runs AFTER the heuristic filter on a reduced candidate set.
Each article gets a 1–10 relevance score and a one-line reason.
"""

import json
from typing import Dict, List

import httpx
import structlog

from app.config import get_settings

logger = structlog.get_logger()

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

FALLBACK_MODELS = [
    "gemini-2.0-flash",
    "gemini-2.5-flash",
    "gemini-2.0-flash-lite",
    "gemini-2.5-flash-lite",
]


class LLMRelevanceFilter:
    """Score articles for relevance using Gemini, then filter by threshold."""

    def __init__(
        self,
        intent_context: str,
        threshold: int = 5,
        timeout: float = 45.0,
    ):
        self.intent_context = intent_context
        self.threshold = threshold
        self.timeout = timeout

    def _build_prompt(self, articles: List[Dict]) -> str:
        """Build a batched relevance-scoring prompt."""
        article_blocks = []
        for i, article in enumerate(articles, 1):
            title = article.get("title", "Untitled")
            content = article.get("raw_content", "")
            # Send first 1500 chars of content for scoring (save tokens)
            if len(content) > 1500:
                content = content[:1500] + "..."
            article_blocks.append(
                f"[ARTICLE {i}]\n"
                f"Title: {title}\n"
                f"Content preview: {content}\n"
            )

        articles_text = "\n---\n".join(article_blocks)

        return f"""You are a relevance scoring system. A user has defined the following intent for their content briefing:

--- USER INTENT ---
{self.intent_context}
--- END INTENT ---

Below are {len(articles)} candidate articles. For EACH article, assess how relevant it is to the user's intent.

Score each article on a scale of 1-10:
- 1-3: Not relevant — topic doesn't match the user's interest
- 4-5: Marginally relevant — tangentially related but not directly useful
- 6-7: Relevant — directly addresses the user's topic area
- 8-10: Highly relevant — precisely matches what the user wants to track

Respond with ONLY a valid JSON array. Each element must have exactly these fields:
- "index": the article number (1-based integer)
- "score": integer 1-10
- "reason": one sentence explaining why this score (max 100 chars)

Do NOT include any text before or after the JSON array.

ARTICLES:
{articles_text}"""

    async def score_and_filter(self, articles: List[Dict]) -> List[Dict]:
        """Score articles via Gemini and filter those below threshold.

        Attaches `llm_relevance_score` and `llm_relevance_reason` to each article.
        Returns only articles scoring >= threshold.
        """
        settings = get_settings()
        log = logger.bind(article_count=len(articles))

        if not articles:
            return articles

        if not settings.GEMINI_API_KEY:
            log.warning("llm_filter.no_api_key", msg="Skipping LLM filter — no API key")
            # Pass all through with neutral scores
            for article in articles:
                article["llm_relevance_score"] = 6
                article["llm_relevance_reason"] = "LLM filter skipped (no API key)"
            return articles

        prompt = self._build_prompt(articles)
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.1,  # Low temp for consistent scoring
                "maxOutputTokens": 2048,
            },
        }

        scores = await self._call_gemini(payload, len(articles), settings.GEMINI_API_KEY)

        if not scores:
            log.warning("llm_filter.no_scores", msg="Gemini returned no scores, passing all")
            for article in articles:
                article["llm_relevance_score"] = 6
                article["llm_relevance_reason"] = "LLM scoring failed — included by default"
            return articles

        # Apply scores and filter
        passed = []
        for i, article in enumerate(articles):
            article_num = i + 1
            score_data = scores.get(article_num, {})
            article["llm_relevance_score"] = score_data.get("score", 5)
            article["llm_relevance_reason"] = score_data.get("reason", "")

            if article["llm_relevance_score"] >= self.threshold:
                passed.append(article)
            else:
                log.debug(
                    "llm_filter.below_threshold",
                    title=article.get("title", "")[:60],
                    score=article["llm_relevance_score"],
                )

        # Sort by LLM score descending
        passed.sort(key=lambda a: a.get("llm_relevance_score", 0), reverse=True)
        return passed

    async def _call_gemini(
        self,
        payload: dict,
        expected_count: int,
        api_key: str,
    ) -> Dict[int, Dict]:
        """Call Gemini with fallback models. Returns {index: {score, reason}}."""
        for model in FALLBACK_MODELS:
            url = GEMINI_BASE_URL.format(model=model)
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    resp = await client.post(
                        url, params={"key": api_key}, json=payload,
                    )

                if resp.status_code == 429:
                    logger.warning("llm_filter.quota_exceeded", model=model)
                    continue

                if resp.status_code != 200:
                    logger.error("llm_filter.api_error", model=model, status=resp.status_code)
                    continue

                data = resp.json()
                candidates = data.get("candidates", [])
                if not candidates:
                    continue

                raw_text = candidates[0]["content"]["parts"][0]["text"]
                return self._parse_scores(raw_text, expected_count)

            except Exception as exc:
                logger.error("llm_filter.exception", model=model, error=str(exc))
                continue

        return {}

    @staticmethod
    def _parse_scores(text: str, expected_count: int) -> Dict[int, Dict]:
        """Parse Gemini's JSON response into {index: {score, reason}}."""
        cleaned = text.strip()

        # Remove markdown code blocks
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            cleaned = "\n".join(lines[1:-1]) if len(lines) > 2 else cleaned

        start = cleaned.find("[")
        end = cleaned.rfind("]")
        if start == -1 or end == -1:
            return {}

        try:
            parsed = json.loads(cleaned[start:end + 1])
            if not isinstance(parsed, list):
                return {}

            result = {}
            for item in parsed:
                if isinstance(item, dict):
                    idx = item.get("index")
                    score = item.get("score", 5)
                    reason = item.get("reason", "")
                    if idx is not None:
                        result[int(idx)] = {
                            "score": int(score),
                            "reason": str(reason)[:200],
                        }
            return result

        except (json.JSONDecodeError, ValueError):
            return {}

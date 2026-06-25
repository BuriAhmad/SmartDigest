"""LLM relevance filter: scores article relevance to user intent.

Runs after the retrieval pre-filter on a reduced candidate set.
Each article gets a 1-10 relevance score and a one-line reason.
"""

from typing import Any, Dict, List

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


class RelevanceScore(BaseModel):
    index: int
    score: int = Field(ge=1, le=10)
    reason: str = ""


class LLMRelevanceFilter:
    """Score articles for relevance using the configured LLM provider."""

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
- 1-3: Not relevant - topic doesn't match the user's interest
- 4-5: Marginally relevant - tangentially related but not directly useful
- 6-7: Relevant - directly addresses the user's topic area
- 8-10: Highly relevant - precisely matches what the user wants to track

Respond with ONLY a valid JSON array. Each element must have exactly these fields:
- "index": the article number (1-based integer)
- "score": integer 1-10
- "reason": one sentence explaining why this score (max 100 chars)

Do NOT include any text before or after the JSON array.

ARTICLES:
{articles_text}"""

    async def score_and_filter(self, articles: List[Dict]) -> List[Dict]:
        """Score articles via the LLM provider and filter below threshold."""
        settings = get_settings()
        log = logger.bind(article_count=len(articles))

        if not articles:
            return articles

        if not get_llm_api_key(settings):
            log.warning("llm_filter.no_api_key", msg="Skipping LLM filter - no API key")
            for article in articles:
                article["llm_relevance_score"] = 6
                article["llm_relevance_reason"] = "LLM filter skipped (no API key)"
            return articles

        prompt = self._build_prompt(articles)
        scores = await self._call_llm(prompt, len(articles), settings)

        if not scores:
            raise RuntimeError("LLM relevance scoring failed: no relevance scores")

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

        passed.sort(key=lambda a: a.get("llm_relevance_score", 0), reverse=True)
        return passed

    async def _call_llm(
        self,
        prompt: str,
        expected_count: int,
        settings: Any,
    ) -> Dict[int, Dict]:
        """Call the shared LLM layer. Returns {index: {score, reason}}."""
        response = await generate_json(
            prompt=prompt,
            response_schema=list[RelevanceScore],
            config=LLMGenerationConfig(
                models=configured_models(
                    _setting(settings, "LLM_RELEVANCE_MODELS", "")
                    or _setting(settings, "GEMINI_RELEVANCE_MODELS", ""),
                    DEFAULT_MODELS,
                ),
                temperature=0.1,
                max_output_tokens=2048,
                timeout_seconds=float(
                    _setting(settings, "LLM_REQUEST_TIMEOUT_SECONDS", 0)
                    or _setting(settings, "GEMINI_REQUEST_TIMEOUT_SECONDS", self.timeout)
                ),
                retry_attempts=int(
                    _setting(settings, "LLM_RETRY_ATTEMPTS", 0)
                    or _setting(settings, "GEMINI_RETRY_ATTEMPTS", 1)
                ),
                retry_backoff_seconds=float(
                    _setting(settings, "LLM_RETRY_BACKOFF_SECONDS", 0)
                    or _setting(settings, "GEMINI_RETRY_BACKOFF_SECONDS", 1.0)
                ),
                log_name="llm_relevance",
            ),
        )
        return self._normalise_scores(response, expected_count)

    @staticmethod
    def _normalise_scores(response: Any, expected_count: int) -> Dict[int, Dict]:
        """Normalise SDK parsed objects or JSON dicts into score records."""
        if not isinstance(response, list):
            return {}

        result: Dict[int, Dict] = {}
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
                score = max(1, min(10, int(data.get("score", 5))))
            except (TypeError, ValueError):
                continue
            result[idx_int] = {
                "score": score,
                "reason": str(data.get("reason", ""))[:200],
            }
        return result


def _setting(settings: Any, name: str, default: Any) -> Any:
    return getattr(settings, name, default)

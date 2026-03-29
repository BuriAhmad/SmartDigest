"""Filter pipeline — runs articles through heuristic + LLM relevance stages.

Usage:
    pipeline = FilterPipeline(intent_context, keywords, exclusion_keywords)
    filtered = await pipeline.run(articles)
"""

from typing import Dict, List, Optional

import structlog

from app.services.filters.heuristic import HeuristicFilter
from app.services.filters.llm_relevance import LLMRelevanceFilter

logger = structlog.get_logger()


class FilterPipeline:
    """Two-stage filter: heuristic pre-filter → LLM relevance scoring."""

    def __init__(
        self,
        intent_context: str,
        keywords: List[str],
        exclusion_keywords: Optional[List[str]] = None,
        heuristic_threshold: float = 0.15,
        llm_threshold: int = 5,
    ):
        self.heuristic = HeuristicFilter(
            keywords=keywords,
            exclusion_keywords=exclusion_keywords or [],
            threshold=heuristic_threshold,
        )
        self.llm_filter = LLMRelevanceFilter(
            intent_context=intent_context,
            threshold=llm_threshold,
        )

    async def run(self, articles: List[Dict]) -> List[Dict]:
        """Run both filter stages. Returns articles with filter metadata attached.

        Each article dict gets:
          - heuristic_score: float 0-1
          - llm_relevance_score: int 1-10 (only if it passed heuristic)
          - llm_relevance_reason: str (only if it passed heuristic)
        """
        log = logger.bind(total_articles=len(articles))

        if not articles:
            return articles

        # Stage 1: Heuristic filter
        heuristic_passed = self.heuristic.filter(articles)
        log.info(
            "filter.heuristic_done",
            input=len(articles),
            passed=len(heuristic_passed),
            dropped=len(articles) - len(heuristic_passed),
        )

        if not heuristic_passed:
            log.warning("filter.all_dropped_by_heuristic")
            return []

        # Stage 2: LLM relevance scoring
        try:
            llm_passed = await self.llm_filter.score_and_filter(heuristic_passed)
            log.info(
                "filter.llm_done",
                input=len(heuristic_passed),
                passed=len(llm_passed),
                dropped=len(heuristic_passed) - len(llm_passed),
            )
        except Exception as exc:
            log.error("filter.llm_failed", error=str(exc))
            # If LLM scoring fails, pass through heuristic results
            llm_passed = heuristic_passed

        log.info(
            "filter.pipeline_complete",
            input=len(articles),
            output=len(llm_passed),
        )

        return llm_passed

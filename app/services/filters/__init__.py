"""Filter pipeline — runs articles through retrieval, reranking, and LLM relevance.

Usage:
    pipeline = FilterPipeline(intent_context, keywords, exclusion_keywords)
    filtered = await pipeline.run(articles)
"""

from typing import Dict, List, Optional

import structlog

from app.config import get_settings
from app.services.filters.bm25 import BM25Filter
from app.services.filters.llm_relevance import LLMRelevanceFilter
from app.services.filters.reranker import CrossEncoderReranker
from app.services.filters.semantic import SemanticRetriever

logger = structlog.get_logger()


class FilterPipeline:
    """Hybrid retriever: BM25 + semantic union -> reranker -> LLM relevance."""

    def __init__(
        self,
        intent_context: str,
        keywords: List[str],
        exclusion_keywords: Optional[List[str]] = None,
        semantic_query: Optional[str] = None,
        lexical_threshold: float = 0.02,
        lexical_top_k: Optional[int] = None,
    ):
        settings = get_settings()
        self.union_max_k = settings.RETRIEVAL_UNION_MAX_K
        semantic_query_text = (semantic_query or intent_context)[
            : settings.SEMANTIC_QUERY_MAX_CHARS
        ]
        reranker_query_text = semantic_query_text
        self.lexical_filter = BM25Filter(
            keywords=keywords,
            exclusion_keywords=exclusion_keywords or [],
            threshold=lexical_threshold,
            top_k=lexical_top_k,
        )
        self.semantic_filter = SemanticRetriever(
            query_text=semantic_query_text,
            exclusion_keywords=exclusion_keywords or [],
            model_name=settings.SEMANTIC_MODEL_NAME,
            top_k=settings.SEMANTIC_TOP_K,
            min_score=settings.SEMANTIC_MIN_SCORE,
            article_max_chars=settings.SEMANTIC_ARTICLE_MAX_CHARS,
            enabled=settings.SEMANTIC_RETRIEVAL_ENABLED,
            local_files_only=settings.SEMANTIC_MODEL_LOCAL_FILES_ONLY,
            load_timeout_seconds=settings.SEMANTIC_MODEL_LOAD_TIMEOUT_SECONDS,
        )
        self.reranker = CrossEncoderReranker(
            query_text=reranker_query_text,
            model_name=settings.RERANKER_MODEL_NAME,
            top_k=settings.RERANKER_TOP_K,
            min_keep=settings.RERANKER_MIN_KEEP,
            min_score=settings.RERANKER_MIN_SCORE,
            max_score_drop=settings.RERANKER_MAX_SCORE_DROP,
            article_max_chars=settings.RERANKER_ARTICLE_MAX_CHARS,
            batch_size=settings.RERANKER_BATCH_SIZE,
            enabled=settings.RERANKER_ENABLED,
            required=settings.RERANKER_REQUIRED,
            local_files_only=settings.RERANKER_MODEL_LOCAL_FILES_ONLY,
            load_timeout_seconds=settings.RERANKER_MODEL_LOAD_TIMEOUT_SECONDS,
        )
        self.llm_filter = LLMRelevanceFilter(
            intent_context=intent_context,
        )

    async def run(self, articles: List[Dict]) -> List[Dict]:
        """Run both filter stages. Returns articles with filter metadata attached.

        Each article dict gets:
          - heuristic_score: float 0-1 (BM25-backed lexical score)
          - bm25_score: raw BM25 score
          - lexical_score: normalised lexical score
          - semantic_score: cosine similarity from semantic retrieval
          - retrieval_score: combined pre-LLM retrieval score
          - retrieval_channels: bm25/semantic channels that retrieved it
          - reranker_score: cross-encoder relevance score
          - reranker_rank: cross-encoder rank before LLM relevance
          - llm_relevance_decision: PASS/FAIL from the final selection gate
          - llm_relevance_score: int 1-10 from the final selection gate
          - llm_relevance_reason: str explaining the final decision
        """
        log = logger.bind(total_articles=len(articles))

        if not articles:
            return articles

        # Stage 1: Hybrid retrieval
        lexical_passed = self.lexical_filter.filter(articles)
        semantic_passed = await self.semantic_filter.retrieve(articles)
        retrieval_passed = self._merge_candidates(lexical_passed, semantic_passed)
        log.info(
            "filter.retrieval_done",
            input=len(articles),
            bm25_passed=len(lexical_passed),
            semantic_passed=len(semantic_passed),
            union_passed=len(retrieval_passed),
            dropped=len(articles) - len(retrieval_passed),
        )

        if not retrieval_passed:
            log.warning("filter.all_dropped_by_retrieval")
            return []

        # Stage 2: Cross-encoder reranking
        reranked_articles = await self.reranker.rerank(retrieval_passed)
        log.info(
            "filter.reranker_done",
            input=len(retrieval_passed),
            passed=len(reranked_articles),
            dropped=len(retrieval_passed) - len(reranked_articles),
        )

        if not reranked_articles:
            log.warning("filter.all_dropped_by_reranker")
            return []

        # Stage 3: LLM relevance scoring
        try:
            llm_passed = await self.llm_filter.score_and_filter(reranked_articles)
            log.info(
                "filter.llm_done",
                input=len(reranked_articles),
                passed=len(llm_passed),
                dropped=len(reranked_articles) - len(llm_passed),
            )
        except Exception as exc:
            log.error("filter.llm_failed", error=str(exc))
            raise

        log.info(
            "filter.pipeline_complete",
            input=len(articles),
            output=len(llm_passed),
        )

        return llm_passed

    def _merge_candidates(
        self,
        lexical_articles: List[Dict],
        semantic_articles: List[Dict],
    ) -> List[Dict]:
        merged: Dict[str, Dict] = {}

        for article in lexical_articles:
            self._add_candidate(merged, article, "bm25")

        for article in semantic_articles:
            self._add_candidate(merged, article, "semantic")

        candidates = list(merged.values())
        for article in candidates:
            channels = article.get("retrieval_channels", [])
            lexical_score = float(article.get("lexical_score") or 0.0)
            semantic_score = float(article.get("semantic_score") or 0.0)
            article["retrieval_score"] = round(
                min(max(lexical_score, semantic_score) + (0.05 if len(channels) > 1 else 0), 1.0),
                3,
            )

        candidates.sort(
            key=lambda article: (
                article.get("retrieval_score", 0.0),
                len(article.get("retrieval_channels", [])),
                article.get("lexical_score", 0.0),
                article.get("semantic_score", 0.0),
            ),
            reverse=True,
        )
        return candidates[: self.union_max_k]

    @staticmethod
    def _add_candidate(
        merged: Dict[str, Dict],
        article: Dict,
        channel: str,
    ) -> None:
        key = FilterPipeline._article_key(article)
        existing = merged.get(key)
        if existing is None:
            article["retrieval_channels"] = [channel]
            merged[key] = article
            return

        channels = existing.setdefault("retrieval_channels", [])
        if channel not in channels:
            channels.append(channel)

        for field in (
            "bm25_score",
            "lexical_score",
            "heuristic_score",
            "semantic_score",
            "semantic_rank",
        ):
            if field not in article:
                continue
            if field not in existing:
                existing[field] = article[field]
            elif field.endswith("_score"):
                existing[field] = max(
                    float(existing[field] or 0.0),
                    float(article[field] or 0.0),
                )

    @staticmethod
    def _article_key(article: Dict) -> str:
        return (
            article.get("url")
            or article.get("item_url")
            or f"{article.get('source_url', '')}:{article.get('title', '')}"
        )

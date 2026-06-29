"""Cross-encoder reranker for the pre-LLM relevance stage."""

import asyncio
from typing import Dict, List, Optional

import structlog

from app.config import get_settings

logger = structlog.get_logger()

_MODEL_CACHE: Dict[str, object] = {}


class CrossEncoderReranker:
    """Rerank retrieval candidates before the LLM relevance scorer."""

    def __init__(
        self,
        query_text: str,
        model_name: str = "cross-encoder/ettin-reranker-68m-v1",
        top_k: int = 10,
        min_keep: int = 5,
        min_score: Optional[float] = None,
        max_score_drop: Optional[float] = None,
        article_max_chars: int = 1800,
        batch_size: int = 8,
        enabled: bool = True,
        required: bool = True,
        local_files_only: bool = True,
        load_timeout_seconds: float = 30.0,
        model: Optional[object] = None,
    ):
        self.query_text = (query_text or "").strip()
        self.model_name = model_name
        self.top_k = max(1, top_k)
        self.min_keep = max(1, min_keep)
        self.min_score = min_score
        self.max_score_drop = max_score_drop
        self.article_max_chars = article_max_chars
        self.batch_size = max(1, batch_size)
        self.enabled = enabled
        self.required = required
        self.local_files_only = local_files_only
        self.load_timeout_seconds = load_timeout_seconds
        self.model = model

    async def rerank(self, articles: List[Dict]) -> List[Dict]:
        if not articles or not self.enabled or not self.query_text:
            return articles

        model = self.model or await self._load_model()
        if model is None:
            if self.required:
                raise RuntimeError(f"Reranker model unavailable: {self.model_name}")
            logger.warning(
                "reranker.unavailable_passthrough",
                model_name=self.model_name,
                input=len(articles),
            )
            return articles

        pairs = [
            (self.query_text, self._article_text(article))
            for article in articles
        ]

        try:
            raw_scores = await asyncio.to_thread(
                model.predict,
                pairs,
                batch_size=self.batch_size,
                show_progress_bar=False,
            )
        except TypeError:
            raw_scores = await asyncio.to_thread(model.predict, pairs)
        except Exception as exc:
            if self.required:
                raise RuntimeError(f"Reranker scoring failed: {exc}") from exc
            logger.warning(
                "reranker.scoring_failed_passthrough",
                model_name=self.model_name,
                error=str(exc),
                input=len(articles),
            )
            return articles

        scores = self._normalise_scores(raw_scores)
        if len(scores) != len(articles):
            message = (
                f"Reranker returned {len(scores)} scores for {len(articles)} articles"
            )
            if self.required:
                raise RuntimeError(message)
            logger.warning("reranker.score_count_mismatch_passthrough", error=message)
            return articles

        scored = []
        for article, score in zip(articles, scores):
            article["reranker_score"] = round(score, 4)
            scored.append(article)

        scored.sort(key=lambda article: article.get("reranker_score", 0.0), reverse=True)
        for rank, article in enumerate(scored, 1):
            article["reranker_rank"] = rank

        selected = self._select(scored)
        logger.info(
            "reranker.complete",
            input=len(articles),
            passed=len(selected),
            top_k=self.top_k,
            min_keep=self.min_keep,
            min_score=self.min_score,
            max_score_drop=self.max_score_drop,
            top_score=scored[0].get("reranker_score") if scored else None,
            bottom_selected_score=selected[-1].get("reranker_score") if selected else None,
        )
        return selected

    async def _load_model(self, raise_on_failure: bool = False) -> Optional[object]:
        if self.model_name in _MODEL_CACHE:
            return _MODEL_CACHE[self.model_name]

        try:
            from sentence_transformers import CrossEncoder
        except Exception as exc:
            logger.warning("reranker.dependency_missing", error=str(exc))
            if raise_on_failure:
                raise RuntimeError(
                    f"Reranker dependency unavailable: {self.model_name}"
                ) from exc
            return None

        try:
            model = await asyncio.wait_for(
                asyncio.to_thread(self._build_cross_encoder, CrossEncoder),
                timeout=self.load_timeout_seconds,
            )
        except Exception as exc:
            logger.warning(
                "reranker.model_load_failed",
                model_name=self.model_name,
                error=str(exc),
            )
            if raise_on_failure:
                raise RuntimeError(
                    "Reranker model unavailable in local cache. "
                    f"Build the image with {self.model_name} baked in and keep "
                    "RERANKER_MODEL_LOCAL_FILES_ONLY=true in production."
                ) from exc
            return None

        _MODEL_CACHE[self.model_name] = model
        return model

    def _build_cross_encoder(self, cross_encoder_cls):
        settings = get_settings()
        kwargs = {
            "local_files_only": self.local_files_only,
        }
        cache_folder = settings.SENTENCE_TRANSFORMERS_HOME or settings.HF_HOME
        if cache_folder:
            kwargs["cache_folder"] = cache_folder
        return cross_encoder_cls(self.model_name, **kwargs)

    def _select(self, scored: List[Dict]) -> List[Dict]:
        if len(scored) <= self.min_keep:
            return scored

        selected: List[Dict] = []
        top_score = float(scored[0].get("reranker_score") or 0.0)

        for article in scored:
            rank = int(article.get("reranker_rank") or 0)
            score = float(article.get("reranker_score") or 0.0)
            if rank <= self.min_keep:
                selected.append(article)
                continue
            if len(selected) >= self.top_k:
                break
            if self.min_score is not None and score < self.min_score:
                continue
            if self.max_score_drop is not None and (top_score - score) > self.max_score_drop:
                continue
            selected.append(article)

        return selected

    def _article_text(self, article: Dict) -> str:
        title = article.get("title") or ""
        tags = ", ".join(article.get("tags") or [])
        source_url = article.get("source_url") or ""
        content = (article.get("raw_content") or "")[: self.article_max_chars]
        return f"Title: {title}\nSource: {source_url}\nTags: {tags}\nContent: {content}"

    @staticmethod
    def _normalise_scores(raw_scores: object) -> List[float]:
        if hasattr(raw_scores, "tolist"):
            raw_scores = raw_scores.tolist()
        if not isinstance(raw_scores, list):
            raw_scores = list(raw_scores)  # type: ignore[arg-type]
        return [float(score) for score in raw_scores]


async def warm_reranker_model(model_name: str) -> bool:
    """Load and cache the reranker model during startup or image build."""
    settings = get_settings()
    reranker = CrossEncoderReranker(
        query_text="startup warmup",
        model_name=model_name,
        local_files_only=settings.RERANKER_MODEL_LOCAL_FILES_ONLY,
        load_timeout_seconds=settings.RERANKER_MODEL_LOAD_TIMEOUT_SECONDS,
    )
    model = await reranker._load_model(raise_on_failure=True)
    return model is not None

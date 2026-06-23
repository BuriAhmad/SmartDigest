"""Semantic embedding retriever for the pre-LLM relevance stage."""

import asyncio
import math
import re
from typing import Dict, Iterable, List, Optional, Pattern

import structlog

logger = structlog.get_logger()

_MODEL_CACHE: Dict[str, object] = {}


class SemanticRetriever:
    """Rank articles by embedding similarity to the user's intent."""

    def __init__(
        self,
        query_text: str,
        exclusion_keywords: Optional[List[str]] = None,
        model_name: str = "sentence-transformers/all-MiniLM-L6-v2",
        top_k: int = 20,
        min_score: float = 0.2,
        article_max_chars: int = 1800,
        enabled: bool = True,
        model: Optional[object] = None,
    ):
        self.query_text = (query_text or "").strip()
        self.exclusion_keywords = [
            kw.lower().strip() for kw in (exclusion_keywords or []) if kw and kw.strip()
        ]
        self.model_name = model_name
        self.top_k = top_k
        self.min_score = min_score
        self.article_max_chars = article_max_chars
        self.enabled = enabled
        self.model = model
        self._excl_patterns = [
            self._compile_precise_pattern(kw) for kw in self.exclusion_keywords
        ]

    async def retrieve(self, articles: List[Dict]) -> List[Dict]:
        if not articles or not self.enabled or not self.query_text:
            return []

        candidates = [article for article in articles if not self._is_excluded(article)]
        if not candidates:
            return []

        model = self.model or await self._load_model()
        if model is None:
            return []

        texts = [self.query_text] + [self._article_text(article) for article in candidates]
        try:
            embeddings = await asyncio.to_thread(
                model.encode,
                texts,
                normalize_embeddings=True,
                show_progress_bar=False,
            )
        except TypeError:
            embeddings = await asyncio.to_thread(model.encode, texts)
        except Exception as exc:
            logger.warning("semantic.embedding_failed", error=str(exc))
            return []

        vectors = [self._as_vector(embedding) for embedding in embeddings]
        if len(vectors) != len(texts):
            logger.warning("semantic.embedding_count_mismatch")
            return []

        query_vector = vectors[0]
        scored = []
        for article, vector in zip(candidates, vectors[1:]):
            score = self._cosine_similarity(query_vector, vector)
            article["semantic_score"] = round(score, 4)
            if score >= self.min_score:
                scored.append(article)

        scored.sort(key=lambda article: article.get("semantic_score", 0.0), reverse=True)
        selected = scored[: self.top_k]
        for rank, article in enumerate(selected, 1):
            article["semantic_rank"] = rank

        logger.info(
            "semantic.complete",
            input=len(articles),
            candidates=len(candidates),
            passed=len(selected),
            min_score=self.min_score,
            top_k=self.top_k,
        )
        return selected

    async def _load_model(self) -> Optional[object]:
        if self.model_name in _MODEL_CACHE:
            return _MODEL_CACHE[self.model_name]

        try:
            from sentence_transformers import SentenceTransformer
        except Exception as exc:
            logger.warning("semantic.dependency_missing", error=str(exc))
            return None

        from app.config import get_settings

        settings = get_settings()
        kwargs = {}
        if settings.SEMANTIC_MODEL_LOCAL_FILES_ONLY:
            kwargs["local_files_only"] = True

        try:
            model = await asyncio.wait_for(
                asyncio.to_thread(SentenceTransformer, self.model_name, **kwargs),
                timeout=settings.SEMANTIC_MODEL_LOAD_TIMEOUT_SECONDS,
            )
        except TypeError as exc:
            if settings.SEMANTIC_MODEL_LOCAL_FILES_ONLY:
                logger.warning(
                    "semantic.model_load_failed",
                    model_name=self.model_name,
                    error=str(exc),
                )
                return None
            model = await asyncio.wait_for(
                asyncio.to_thread(SentenceTransformer, self.model_name),
                timeout=settings.SEMANTIC_MODEL_LOAD_TIMEOUT_SECONDS,
            )
        except Exception as exc:
            logger.warning(
                "semantic.model_load_failed",
                model_name=self.model_name,
                error=str(exc),
            )
            return None

        _MODEL_CACHE[self.model_name] = model
        return model

    def _is_excluded(self, article: Dict) -> bool:
        haystack = " ".join([
            article.get("title") or "",
            article.get("raw_content") or "",
        ])
        return any(pattern.search(haystack) for pattern in self._excl_patterns)

    def _article_text(self, article: Dict) -> str:
        title = article.get("title") or ""
        tags = ", ".join(article.get("tags") or [])
        content = (article.get("raw_content") or "")[: self.article_max_chars]
        return f"Title: {title}\nTags: {tags}\nContent: {content}"

    @staticmethod
    def _as_vector(value: object) -> List[float]:
        if hasattr(value, "tolist"):
            value = value.tolist()
        return [float(item) for item in value]  # type: ignore[arg-type]

    @staticmethod
    def _cosine_similarity(left: Iterable[float], right: Iterable[float]) -> float:
        left_values = list(left)
        right_values = list(right)
        if not left_values or not right_values or len(left_values) != len(right_values):
            return 0.0

        dot = sum(a * b for a, b in zip(left_values, right_values))
        left_norm = math.sqrt(sum(a * a for a in left_values))
        right_norm = math.sqrt(sum(b * b for b in right_values))
        if left_norm == 0 or right_norm == 0:
            return 0.0
        return dot / (left_norm * right_norm)

    @staticmethod
    def _compile_precise_pattern(value: str) -> Pattern[str]:
        escaped = re.escape(value.strip())
        escaped = escaped.replace(r"\ ", r"\s+")
        return re.compile(rf"(?<![A-Za-z0-9]){escaped}(?![A-Za-z0-9])", re.IGNORECASE)


async def warm_semantic_model(model_name: str) -> bool:
    """Load and cache a semantic model during startup.

    Returns True when the model is available in cache after warmup, and False
    when loading is skipped or fails.
    """
    retriever = SemanticRetriever(query_text="startup warmup", model_name=model_name)
    model = await retriever._load_model()
    return model is not None

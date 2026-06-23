"""BM25 lexical filter for the pre-LLM relevance stage.

The ranking math is provided by `rank-bm25`; this module only adapts article
data into tokens and applies product-specific gates like exclusions and top-k.
"""

import re
from math import ceil
from typing import Dict, Iterable, List, Optional, Pattern

import structlog
from rank_bm25 import BM25Plus

logger = structlog.get_logger()

TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9+#.\-]*", re.IGNORECASE)

STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "how",
    "in",
    "into",
    "is",
    "it",
    "of",
    "on",
    "or",
    "that",
    "the",
    "this",
    "to",
    "with",
}


class BM25Filter:
    """Rank and filter articles using package-backed BM25+."""

    def __init__(
        self,
        keywords: List[str],
        exclusion_keywords: Optional[List[str]] = None,
        threshold: float = 0.02,
        top_k: Optional[int] = None,
        max_dynamic_top_k: int = 50,
    ):
        self.keywords = [kw.lower().strip() for kw in keywords if kw and kw.strip()]
        self.exclusion_keywords = [
            kw.lower().strip() for kw in (exclusion_keywords or []) if kw and kw.strip()
        ]
        self.query_terms = self._dedupe(self._tokenize_many(self.keywords))
        self.threshold = threshold
        self.top_k = top_k
        self.max_dynamic_top_k = max_dynamic_top_k

        self._excl_patterns = [
            self._compile_precise_pattern(kw) for kw in self.exclusion_keywords
        ]
        self._phrase_patterns = [
            self._compile_precise_pattern(kw) for kw in self.keywords if kw
        ]

    def filter(self, articles: List[Dict]) -> List[Dict]:
        """Attach BM25 metadata and return the strongest lexical candidates."""
        if not articles:
            return articles

        candidates = []
        excluded_count = 0
        for article in articles:
            if self._is_excluded(article):
                excluded_count += 1
                article["heuristic_score"] = 0.0
                article["bm25_score"] = 0.0
                article["lexical_score"] = 0.0
                logger.debug(
                    "bm25.excluded",
                    title=article.get("title", "")[:60],
                    reason="exclusion_keyword",
                )
                continue
            candidates.append(article)

        if not candidates:
            return []

        if not self.query_terms:
            return self._pass_through(candidates)

        tokenized_corpus = [self._article_tokens(article) for article in candidates]
        if not any(tokenized_corpus):
            return self._pass_through(candidates)

        bm25 = BM25Plus(tokenized_corpus)
        raw_scores = bm25.get_scores(self.query_terms)
        adjusted_scores = [
            float(raw_score) if self._has_query_overlap(tokens) else 0.0
            for tokens, raw_score in zip(tokenized_corpus, raw_scores)
        ]
        max_raw_score = max(adjusted_scores) if adjusted_scores else 0.0

        passed = []
        for article, tokens, raw_score in zip(candidates, tokenized_corpus, adjusted_scores):
            normalized_bm25 = float(raw_score / max_raw_score) if max_raw_score > 0 else 0.0
            phrase_boost = self._exact_phrase_boost(article)
            coverage_boost = self._coverage_boost(tokens)
            lexical_score = min((normalized_bm25 * 0.75) + phrase_boost + coverage_boost, 1.0)

            article["bm25_score"] = round(float(raw_score), 4)
            article["lexical_score"] = round(lexical_score, 3)
            article["heuristic_score"] = article["lexical_score"]

            if lexical_score >= self.threshold:
                passed.append(article)
            else:
                logger.debug(
                    "bm25.below_threshold",
                    title=article.get("title", "")[:60],
                    score=round(lexical_score, 3),
                )

        passed.sort(
            key=lambda article: (
                article.get("lexical_score", 0.0),
                article.get("bm25_score", 0.0),
            ),
            reverse=True,
        )
        limit = self._candidate_limit(len(candidates))
        selected = passed[:limit]
        logger.info(
            "bm25.complete",
            input=len(articles),
            excluded=excluded_count,
            candidates=len(candidates),
            passed=len(selected),
            threshold=self.threshold,
            top_k=limit,
        )
        return selected

    def _is_excluded(self, article: Dict) -> bool:
        title = article.get("title") or ""
        content = article.get("raw_content") or ""
        haystack = f"{title} {content}"
        return any(pattern.search(haystack) for pattern in self._excl_patterns)

    def _pass_through(self, articles: List[Dict]) -> List[Dict]:
        for article in articles:
            article["bm25_score"] = 0.0
            article["lexical_score"] = 1.0
            article["heuristic_score"] = 1.0
        return articles[: self._candidate_limit(len(articles))]

    def _article_tokens(self, article: Dict) -> List[str]:
        title = article.get("title") or ""
        content = article.get("raw_content") or ""
        tags = " ".join(article.get("tags") or [])
        weighted_text = f"{title} {title} {title} {tags} {tags} {content}"
        return self._tokenize(weighted_text)

    def _exact_phrase_boost(self, article: Dict) -> float:
        title = article.get("title") or ""
        content = article.get("raw_content") or ""
        tags = " ".join(article.get("tags") or [])
        boost = 0.0

        for pattern in self._phrase_patterns:
            if pattern.search(title):
                boost += 0.15
            elif pattern.search(tags):
                boost += 0.10
            elif pattern.search(content):
                boost += 0.05

        return min(boost, 0.30)

    def _has_query_overlap(self, tokens: List[str]) -> bool:
        token_set = set(tokens)
        return any(term in token_set for term in self.query_terms)

    def _coverage_boost(self, tokens: List[str]) -> float:
        if not self.query_terms:
            return 0.0
        matched = len(set(tokens).intersection(self.query_terms))
        return min((matched / len(self.query_terms)) * 0.15, 0.15)

    def _candidate_limit(self, candidate_count: int) -> int:
        if self.top_k is not None:
            return min(self.top_k, candidate_count)
        if candidate_count <= 20:
            return candidate_count
        return min(max(20, ceil(candidate_count * 0.6)), self.max_dynamic_top_k, candidate_count)

    @classmethod
    def _tokenize_many(cls, values: Iterable[str]) -> List[str]:
        tokens: List[str] = []
        for value in values:
            tokens.extend(cls._tokenize(value))
        return tokens

    @staticmethod
    def _tokenize(value: str) -> List[str]:
        tokens: List[str] = []
        for raw_token in TOKEN_RE.findall(value or ""):
            for token in raw_token.replace("-", " ").split():
                normalised = BM25Filter._normalise_token(token)
                if len(normalised) > 1 and normalised not in STOPWORDS:
                    tokens.append(normalised)
        return tokens

    @staticmethod
    def _normalise_token(token: str) -> str:
        token = token.lower().strip(".,;:!?\"'()[]{}")
        if len(token) > 4 and token.endswith("ies"):
            return f"{token[:-3]}y"
        if len(token) > 4 and (
            token.endswith(("ches", "shes")) or token[-3] in {"s", "x", "z"}
        ):
            return token[:-2]
        if len(token) > 3 and token.endswith("s"):
            return token[:-1]
        return token

    @staticmethod
    def _compile_precise_pattern(value: str) -> Pattern[str]:
        escaped = re.escape(value.strip())
        escaped = escaped.replace(r"\ ", r"\s+")
        return re.compile(rf"(?<![A-Za-z0-9]){escaped}(?![A-Za-z0-9])", re.IGNORECASE)

    @staticmethod
    def _dedupe(values: Iterable[str]) -> List[str]:
        seen = set()
        deduped = []
        for value in values:
            if value not in seen:
                seen.add(value)
                deduped.append(value)
        return deduped

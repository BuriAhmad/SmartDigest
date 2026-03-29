"""Heuristic filter — fast keyword-based pre-filter.

Runs BEFORE the LLM to eliminate clearly irrelevant articles.
Uses keyword matching in titles and content, exclusion keywords,
and source tag overlap.
"""

import re
from typing import Dict, List

import structlog

logger = structlog.get_logger()


class HeuristicFilter:
    """Score and filter articles using keyword heuristics.

    Scoring signals (weights):
      - Keyword match in title:   0.35 per keyword
      - Keyword match in content: 0.15 per keyword
      - Source tag overlap:       0.20 per overlapping tag
      - Exclusion keyword match: -1.00 (instant reject)

    Score is normalised to 0.0–1.0 and capped at 1.0.
    Articles below the threshold are dropped.
    """

    def __init__(
        self,
        keywords: List[str],
        exclusion_keywords: List[str] = None,
        threshold: float = 0.15,
    ):
        self.keywords = [kw.lower().strip() for kw in keywords if kw.strip()]
        self.exclusion_keywords = [
            kw.lower().strip() for kw in (exclusion_keywords or []) if kw.strip()
        ]
        self.threshold = threshold

        # Pre-compile regex patterns for multi-word keywords
        self._kw_patterns = [
            re.compile(re.escape(kw), re.IGNORECASE) for kw in self.keywords
        ]
        self._excl_patterns = [
            re.compile(re.escape(kw), re.IGNORECASE) for kw in self.exclusion_keywords
        ]

    def score(self, article: Dict) -> float:
        """Compute a heuristic relevance score for a single article.

        Returns a float 0.0–1.0. Returns -1.0 if exclusion keyword matched.
        """
        title = (article.get("title") or "").lower()
        content = (article.get("raw_content") or "").lower()
        tags = [t.lower() for t in (article.get("tags") or [])]
        combined = f"{title} {content}"

        # Check exclusion keywords first (instant reject)
        for pattern in self._excl_patterns:
            if pattern.search(title) or pattern.search(content):
                return -1.0

        score = 0.0

        # Keyword matches
        for pattern in self._kw_patterns:
            if pattern.search(title):
                score += 0.35
            if pattern.search(content):
                score += 0.15

        # Tag overlap
        for kw in self.keywords:
            for tag in tags:
                if kw in tag or tag in kw:
                    score += 0.20
                    break

        # Normalise: cap at 1.0
        return min(score, 1.0)

    def filter(self, articles: List[Dict]) -> List[Dict]:
        """Score and filter a list of articles. Returns those above threshold.

        Attaches `heuristic_score` to each article dict.
        """
        passed = []
        for article in articles:
            score = self.score(article)

            if score < 0:
                # Exclusion keyword matched — hard reject
                article["heuristic_score"] = 0.0
                logger.debug(
                    "heuristic.excluded",
                    title=article.get("title", "")[:60],
                    reason="exclusion_keyword",
                )
                continue

            article["heuristic_score"] = round(score, 3)

            if score >= self.threshold:
                passed.append(article)
            else:
                logger.debug(
                    "heuristic.below_threshold",
                    title=article.get("title", "")[:60],
                    score=round(score, 3),
                )

        # Sort by score descending so highest-relevance articles come first
        passed.sort(key=lambda a: a.get("heuristic_score", 0), reverse=True)
        return passed

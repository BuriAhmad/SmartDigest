"""Intent context builder.

Assembles a briefing's structured intent fields into a single rich context
string that downstream services (filters, summariser) can consume.
"""

import re
from typing import List, Optional


TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9+#.\-]*", re.IGNORECASE)

STOPWORDS = {
    "about",
    "after",
    "also",
    "and",
    "are",
    "because",
    "been",
    "being",
    "for",
    "from",
    "have",
    "into",
    "that",
    "the",
    "their",
    "this",
    "track",
    "want",
    "what",
    "when",
    "where",
    "which",
    "with",
}


def build_intent_context(
    topic: str,
    intent_description: Optional[str] = None,
    keywords: Optional[List[str]] = None,
    example_articles: Optional[List[str]] = None,
    exclusion_keywords: Optional[List[str]] = None,
) -> str:
    """Build a rich intent context string from structured fields.

    Example output:
        Topic: AI in Healthcare
        Intent: Track practical LLM applications in clinical settings,
                especially NLP for medical records and diagnostic AI.
        Keywords: LLM, clinical NLP, medical AI, diagnostics
        Exclude: cryptocurrency, gaming, social media
        Example articles the user finds valuable:
        - "How GPT-4 is transforming radiology workflows"
    """
    parts = [f"Topic: {topic}"]

    if intent_description:
        parts.append(f"Intent: {intent_description}")

    if keywords:
        parts.append(f"Keywords: {', '.join(keywords)}")

    if exclusion_keywords:
        parts.append(f"Exclude: {', '.join(exclusion_keywords)}")

    if example_articles:
        examples = "\n".join(f"  - {a}" for a in example_articles)
        parts.append(f"Example articles the user finds valuable:\n{examples}")

    return "\n".join(parts)


def extract_all_keywords(
    keywords: Optional[List[str]] = None,
    topic: Optional[str] = None,
    intent_description: Optional[str] = None,
    example_articles: Optional[List[str]] = None,
) -> List[str]:
    """Extract a flat list of keyword-like terms for lexical retrieval.

    Combines explicit keywords with significant words from the topic, intent,
    and any example article text. URLs are ignored because they are weak signals.
    """
    all_kw: set[str] = set()

    if keywords:
        for kw in keywords:
            clean_kw = kw.strip().lower()
            if clean_kw:
                all_kw.add(clean_kw)

    for text in (topic, intent_description):
        all_kw.update(_extract_terms(text))

    for example in example_articles or []:
        if example and not example.lower().startswith(("http://", "https://")):
            all_kw.update(_extract_terms(example))

    return sorted(all_kw)


def build_semantic_query(
    topic: str,
    intent_description: Optional[str] = None,
    keywords: Optional[List[str]] = None,
    example_articles: Optional[List[str]] = None,
    max_chars: int = 2000,
) -> str:
    """Build natural-language query text for embedding similarity."""
    parts = [f"Topic: {topic}"]

    if intent_description:
        parts.append(f"Intent: {intent_description}")

    if keywords:
        parts.append(f"Keywords: {', '.join(keywords)}")

    examples = [
        example.strip()
        for example in (example_articles or [])
        if example and not example.lower().startswith(("http://", "https://"))
    ]
    if examples:
        parts.append("Examples: " + " | ".join(examples[:3]))

    return "\n".join(parts)[:max_chars]


def _extract_terms(text: Optional[str]) -> set[str]:
    if not text:
        return set()

    terms: set[str] = set()
    tokens = [
        _normalise_token(token)
        for token in TOKEN_RE.findall(text)
    ]
    tokens = [
        token for token in tokens
        if len(token) >= 3 and token not in STOPWORDS
    ]

    terms.update(tokens)

    # Preserve short adjacent phrases from natural-language intent so BM25 can
    # reward concepts like "electric vehicles" and "clinical trials".
    for i in range(len(tokens) - 1):
        terms.add(f"{tokens[i]} {tokens[i + 1]}")

    return terms


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

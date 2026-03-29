"""Intent context builder.

Assembles a briefing's structured intent fields into a single rich context
string that downstream services (filters, summariser) can consume.
"""

from typing import List, Optional


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
) -> List[str]:
    """Extract a flat list of all keyword-like terms for heuristic matching.

    Combines explicit keywords with significant words from the topic.
    Returns lowercase, deduplicated.
    """
    all_kw: set[str] = set()

    if keywords:
        for kw in keywords:
            all_kw.add(kw.strip().lower())

    if topic:
        # Split topic into words, keep those >= 3 chars (skip "in", "of", etc.)
        for word in topic.split():
            clean = word.strip().lower().strip(",.;:!?\"'()")
            if len(clean) >= 3:
                all_kw.add(clean)

    return sorted(all_kw)

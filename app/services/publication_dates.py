"""Publication date resolution helpers.

The resolver keeps date extraction policy in one place so scrapers can report
where a date came from instead of storing only a nullable timestamp.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from typing import Any, Dict, Iterable, List, Optional


HIGH = "high"
MEDIUM = "medium"
LOW = "low"

STATUS_RESOLVED = "resolved"
STATUS_UPDATED_ONLY = "updated_only"
STATUS_UNRESOLVED = "unresolved"

JSON_LD_RE = re.compile(
    r"<script\b[^>]*type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>",
    re.IGNORECASE | re.DOTALL,
)
META_RE = re.compile(r"<meta\b[^>]*>", re.IGNORECASE)
TIME_RE = re.compile(r"<time\b[^>]*>", re.IGNORECASE)
ATTR_RE = re.compile(
    r"([:\w-]+)\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)",
    re.IGNORECASE,
)

PUBLISHED_META_KEYS = {
    "article:published_time",
    "datepublished",
    "date",
    "dc.date",
    "dc:date",
    "pubdate",
    "publishdate",
    "publish_date",
    "sailthru.date",
}

UPDATED_META_KEYS = {
    "article:modified_time",
    "datemodified",
    "lastmod",
    "last-modified",
    "modified",
    "updated",
}


@dataclass
class DateCandidate:
    """A single possible date extracted from feed, page, or headers."""

    value: datetime
    source: str
    confidence: str
    kind: str

    def to_dict(self) -> Dict[str, str]:
        return {
            "value": self.value.isoformat(),
            "source": self.source,
            "confidence": self.confidence,
            "kind": self.kind,
        }


@dataclass
class PublicationDateResult:
    """Resolved publication/updated dates plus provenance."""

    published_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    source: str = "unknown"
    confidence: str = LOW
    status: str = STATUS_UNRESOLVED
    candidates: List[DateCandidate] = field(default_factory=list)

    def to_article_fields(self) -> Dict[str, Any]:
        return {
            "published_at": self.published_at,
            "updated_at": self.updated_at,
            "date_source": self.source,
            "date_confidence": self.confidence,
            "date_resolution_status": self.status,
            "date_candidates": [candidate.to_dict() for candidate in self.candidates],
        }


def resolve_publication_date(
    *,
    feed_entry: Any = None,
    html: Optional[str] = None,
    url: str = "",
    response_headers: Optional[Dict[str, str]] = None,
) -> PublicationDateResult:
    """Resolve the best publication date from feed and page metadata."""

    candidates: List[DateCandidate] = []
    candidates.extend(_feed_candidates(feed_entry))

    if html:
        candidates.extend(_json_ld_candidates(html))
        candidates.extend(_meta_candidates(html))
        candidates.extend(_time_candidates(html))
        candidates.extend(_trafilatura_candidates(html, url))

    if response_headers:
        candidates.extend(_header_candidates(response_headers))

    published = _first_candidate(candidates, "published")
    updated = _first_candidate(candidates, "updated")

    if published:
        return PublicationDateResult(
            published_at=published.value,
            updated_at=updated.value if updated else None,
            source=published.source,
            confidence=published.confidence,
            status=STATUS_RESOLVED,
            candidates=candidates,
        )

    if updated:
        return PublicationDateResult(
            published_at=None,
            updated_at=updated.value,
            source=updated.source,
            confidence=updated.confidence,
            status=STATUS_UPDATED_ONLY,
            candidates=candidates,
        )

    return PublicationDateResult(candidates=candidates)


def is_newer_than_window(result: PublicationDateResult, since: datetime) -> bool:
    """Only a resolved publication date can satisfy the moving window."""

    return bool(result.published_at and result.published_at > since)


def _feed_candidates(entry: Any) -> List[DateCandidate]:
    if entry is None:
        return []

    candidates: List[DateCandidate] = []
    published = _coerce_datetime(getattr(entry, "published_parsed", None))
    if published is None:
        published = _coerce_datetime(getattr(entry, "published", None))
    if published:
        candidates.append(DateCandidate(published, "feed_published", HIGH, "published"))

    updated = _coerce_datetime(getattr(entry, "updated_parsed", None))
    if updated is None:
        updated = _coerce_datetime(getattr(entry, "updated", None))
    if updated:
        candidates.append(DateCandidate(updated, "feed_updated", MEDIUM, "updated"))

    return candidates


def _json_ld_candidates(html: str) -> List[DateCandidate]:
    candidates: List[DateCandidate] = []
    for raw_json in JSON_LD_RE.findall(html):
        try:
            payload = json.loads(unescape(raw_json).strip())
        except Exception:
            continue
        for item in _walk_json(payload):
            if not isinstance(item, dict):
                continue
            published = _coerce_datetime(
                item.get("datePublished") or item.get("dateCreated") or item.get("uploadDate")
            )
            if published:
                candidates.append(
                    DateCandidate(published, "json_ld_date_published", HIGH, "published")
                )
            updated = _coerce_datetime(item.get("dateModified"))
            if updated:
                candidates.append(
                    DateCandidate(updated, "json_ld_date_modified", MEDIUM, "updated")
                )
    return candidates


def _meta_candidates(html: str) -> List[DateCandidate]:
    candidates: List[DateCandidate] = []
    for tag in META_RE.findall(html):
        attrs = _attrs(tag)
        key = (
            attrs.get("property")
            or attrs.get("name")
            or attrs.get("itemprop")
            or attrs.get("http-equiv")
            or ""
        ).strip().lower()
        content = attrs.get("content") or attrs.get("value")
        value = _coerce_datetime(content)
        if not key or not value:
            continue
        normalized_key = key.replace("_", "").replace("-", "")
        if key in PUBLISHED_META_KEYS or normalized_key in PUBLISHED_META_KEYS:
            candidates.append(DateCandidate(value, f"meta_{key}", HIGH, "published"))
        elif key in UPDATED_META_KEYS or normalized_key in UPDATED_META_KEYS:
            candidates.append(DateCandidate(value, f"meta_{key}", MEDIUM, "updated"))
    return candidates


def _time_candidates(html: str) -> List[DateCandidate]:
    candidates: List[DateCandidate] = []
    for tag in TIME_RE.findall(html):
        attrs = _attrs(tag)
        value = _coerce_datetime(attrs.get("datetime") or attrs.get("content"))
        if value:
            candidates.append(DateCandidate(value, "html_time", MEDIUM, "published"))
    return candidates


def _trafilatura_candidates(html: str, url: str) -> List[DateCandidate]:
    try:
        import trafilatura

        metadata = trafilatura.extract_metadata(html, default_url=url or None)
    except Exception:
        return []

    if not metadata:
        return []

    candidates: List[DateCandidate] = []
    published = _coerce_datetime(getattr(metadata, "date", None))
    if published:
        candidates.append(DateCandidate(published, "trafilatura_metadata", MEDIUM, "published"))
    return candidates


def _header_candidates(headers: Dict[str, str]) -> List[DateCandidate]:
    last_modified = None
    for key, value in headers.items():
        if key.lower() == "last-modified":
            last_modified = value
            break
    parsed = _coerce_datetime(last_modified)
    if not parsed:
        return []
    return [DateCandidate(parsed, "http_last_modified", LOW, "updated")]


def _first_candidate(candidates: List[DateCandidate], kind: str) -> Optional[DateCandidate]:
    for candidate in candidates:
        if candidate.kind == kind:
            return candidate
    return None


def _walk_json(value: Any) -> Iterable[Any]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_json(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_json(child)


def _attrs(tag: str) -> Dict[str, str]:
    attrs: Dict[str, str] = {}
    for key, raw_value in ATTR_RE.findall(tag):
        value = raw_value.strip("\"'")
        attrs[key.lower()] = unescape(value)
    return attrs


def _coerce_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return _ensure_utc(value)
    if isinstance(value, (tuple, list)) and len(value) >= 6:
        try:
            return datetime(*value[:6], tzinfo=timezone.utc)
        except Exception:
            return None
    if hasattr(value, "tm_year") and hasattr(value, "tm_mon") and hasattr(value, "tm_mday"):
        try:
            return datetime(
                value.tm_year,
                value.tm_mon,
                value.tm_mday,
                value.tm_hour,
                value.tm_min,
                value.tm_sec,
                tzinfo=timezone.utc,
            )
        except Exception:
            return None
    if not isinstance(value, str):
        return None

    text = value.strip()
    if not text:
        return None

    parsed = _parse_iso_datetime(text) or _parse_rfc_datetime(text) or _parse_common_datetime(text)
    return _ensure_utc(parsed) if parsed else None


def _parse_iso_datetime(text: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _parse_rfc_datetime(text: str) -> Optional[datetime]:
    try:
        return parsedate_to_datetime(text)
    except Exception:
        return None


def _parse_common_datetime(text: str) -> Optional[datetime]:
    formats = [
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%d %B %Y",
        "%B %d, %Y",
        "%b %d, %Y",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(text[:32], fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)

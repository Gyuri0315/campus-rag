"""Priority scoring policy for rule documents.

Rule priority is a retrieval-time trust signal. It should not replace semantic
similarity; it should only promote more authoritative, student-facing, current,
and usable rule sources among otherwise relevant results.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable
from datetime import date, datetime
from typing import Any

DATE_PATTERN = re.compile(r"(20\d{2})[.\-/\ub144]\s*(\d{1,2})[.\-/\uc6d4]\s*(\d{1,2})")

DEPRECATED_KEYWORDS = (
    "\ud3d0\uc9c0",
    "\ud3d0\uae30",
    "\uc0ad\uc81c",
    "\uc2e4\ud6a8",
    "\uc885\ub8cc",
)

HIGH_STUDENT_KEYWORDS = (
    "\uc878\uc5c5",
    "\uad50\uc721\uacfc\uc815",
    "\uad50\uacfc\uacfc\uc815",
    "\ud559\uc810",
    "\uc774\uc218",
    "\uc218\uac15",
    "\uacc4\uc808\uc218\uc5c5",
    "\ub300\uccb4\uacfc\ubaa9",
    "\ub3d9\uc77c\uacfc\ubaa9",
    "\uc131\uc801",
    "\uc2dc\ud5d8",
    "\uc804\uacf5",
    "\uad50\uc591",
    "\ub2e4\uc804\uacf5",
    "\ubcf5\uc218\uc804\uacf5",
    "\ubd80\uc804\uacf5",
    "\ud3b8\uc785",
    "\uc804\uacfc",
    "\ud734\ud559",
    "\ubcf5\ud559",
    "\uc7a5\ud559",
    "\ud559\uc801",
    "\ud604\uc7a5\uc2e4\uc2b5",
    "\ucea1\uc2a4\ud1a4",
)

MEDIUM_STUDENT_KEYWORDS = (
    "\ube44\uad50\uacfc",
    "\ub9c8\uc77c\ub9ac\uc9c0",
    "\ucde8\uc5c5",
    "\ucc3d\uc5c5",
    "\uc0c1\ub2f4",
    "\ubd09\uc0ac",
    "\ud559\uc0dd",
    "\ud559\ubd80",
)

LOW_STUDENT_KEYWORDS = (
    "\uc5f0\uad6c\uc6d0",
    "\uc13c\ud130",
    "\uc0b0\ud559\ud611\ub825\ub2e8",
    "\uc704\uc6d0\ud68c",
    "\uad50\uc6d0",
    "\uc9c1\uc6d0",
    "\uc608\uc0b0",
    "\ud68c\uacc4",
    "\ucd9c\uc7a5",
    "\uacf5\uac04",
    "\uc2dc\uc124",
    "\ubcf4\uc548",
    "\uc815\ubcf4\ud654",
)


def normalize_text(value: object) -> str:
    return " ".join(str(value or "").replace("\u00a0", " ").split()).lower()


def parse_rule_date(value: object) -> date | None:
    if not value:
        return None
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%Y.%m.%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    match = DATE_PATTERN.search(text)
    if not match:
        return None
    year, month, day = (int(part) for part in match.groups())
    try:
        return date(year, month, day)
    except ValueError:
        return None


def extract_latest_date(*values: object) -> date | None:
    dates: list[date] = []
    for value in values:
        text = str(value or "")
        parsed = parse_rule_date(text)
        if parsed:
            dates.append(parsed)
        for year, month, day in DATE_PATTERN.findall(text):
            try:
                dates.append(date(int(year), int(month), int(day)))
            except ValueError:
                continue
    return max(dates) if dates else None


def authority_score(metadata: dict[str, Any]) -> float:
    subcategory = normalize_text(metadata.get("subcategory"))
    doc_type = normalize_text(metadata.get("doc_type"))

    if subcategory == "school_rule" or doc_type == "hak":
        return 1.0
    if subcategory == "regulation" or doc_type == "gyu":
        return 0.9
    if subcategory == "bylaw":
        return 0.75
    if subcategory == "guideline":
        return 0.65
    return 0.55


def student_relevance_score(title: str, content: str) -> float:
    haystack = normalize_text(f"{title}\n{content[:5000]}")
    high = sum(1 for keyword in HIGH_STUDENT_KEYWORDS if keyword in haystack)
    medium = sum(1 for keyword in MEDIUM_STUDENT_KEYWORDS if keyword in haystack)
    low = sum(1 for keyword in LOW_STUDENT_KEYWORDS if keyword in haystack)

    positive = min(1.0, high * 0.25 + medium * 0.10)
    penalty = min(0.5, low * 0.10)
    return max(0.15, min(1.0, 0.25 + positive - penalty))


def recency_score(rule_date: date | None, *, today: date | None = None) -> float:
    if rule_date is None:
        return 0.45
    today = today or date.today()
    age_days = max(0, (today - rule_date).days)
    age_years = age_days / 365.25
    if age_years <= 1:
        return 1.0
    if age_years <= 3:
        return 0.8
    if age_years <= 7:
        return 0.55
    return 0.3


def source_quality_score(metadata: dict[str, Any], content: str) -> float:
    length_score = min(1.0, math.log10(max(len(content), 10)) / 4.0)
    kind = normalize_text(metadata.get("source_kind"))
    ext = normalize_text(metadata.get("source_ext"))
    kind_score = 0.75
    if kind == "file":
        kind_score = 1.0
    elif kind == "html":
        kind_score = 0.85
    elif kind == "json":
        kind_score = 0.75
    if ext in {".hwp", ".hwpx", ".pdf", ".docx", ".doc"}:
        kind_score = max(kind_score, 0.95)

    has_title = bool(normalize_text(metadata.get("doc_title") or metadata.get("source_file")))
    has_url = bool(normalize_text(metadata.get("doc_url") or metadata.get("source_page_url")))
    metadata_score = 0.5 + (0.25 if has_title else 0.0) + (0.25 if has_url else 0.0)
    return min(1.0, 0.45 * length_score + 0.35 * kind_score + 0.20 * metadata_score)


def is_deprecated(title: str, content: str) -> bool:
    haystack = normalize_text(f"{title}\n{content[:1000]}")
    return any(keyword in haystack for keyword in DEPRECATED_KEYWORDS)


def calculate_rule_priority(
    *,
    title: str,
    content: str,
    metadata: dict[str, Any],
    today: date | None = None,
) -> tuple[float, dict[str, float | int | str | bool | None]]:
    latest_date = extract_latest_date(
        metadata.get("date"),
        metadata.get("doc_title"),
        metadata.get("source_file"),
        title,
        content[:1500],
    )
    authority = authority_score(metadata)
    student_relevance = student_relevance_score(title, content)
    recency = recency_score(latest_date, today=today)
    source_quality = source_quality_score(metadata, content)
    deprecated = is_deprecated(title, content)

    score = (
        0.30 * authority
        + 0.40 * student_relevance
        + 0.15 * recency
        + 0.15 * source_quality
    )
    if deprecated:
        score *= 0.2

    score = max(0.0, min(1.0, score))
    return score, {
        "rule": "rule_authority_student_recency_quality",
        "authority_score": authority,
        "student_relevance_score": student_relevance,
        "recency_score": recency,
        "source_quality_score": source_quality,
        "deprecated": deprecated,
        "latest_rule_date": latest_date.isoformat() if latest_date else None,
        "content_chars": len(content),
    }


def aggregate_source_records(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    sources: dict[str, dict[str, Any]] = {}
    for record in records:
        metadata = record.get("metadata") or {}
        if not isinstance(metadata, dict):
            metadata = {}
        source_slug = str(record.get("source_slug") or metadata.get("source_slug") or "")
        if not source_slug:
            continue
        source = sources.setdefault(
            source_slug,
            {
                "id": source_slug,
                "title": metadata.get("doc_title") or metadata.get("source_file") or source_slug,
                "metadata": metadata,
                "content_parts": [],
            },
        )
        source["content_parts"].append(str(record.get("text") or record.get("content") or ""))
    return [
        {
            "id": source["id"],
            "title": source["title"],
            "metadata": source["metadata"],
            "content": "\n\n".join(source["content_parts"]),
        }
        for source in sources.values()
    ]


"""Priority scoring policy for CE source documents."""

from __future__ import annotations

import re
from collections import Counter
from datetime import date, datetime
from typing import Iterable

TOKEN_PATTERN = re.compile(r"[0-9a-zA-Z\uac00-\ud7a3]+")
DATE_PATTERN = re.compile(
    r"(20\d{2})(?:[.\-/\ub144]|\s*\ud559\ub144\ub3c4)\s*(\d{1,2})?[.\-/\uc6d4]?\s*(\d{1,2})?"
)


def normalize_text(text: str) -> str:
    return " ".join((text or "").replace("\u00a0", " ").lower().split())


def content_features(text: str) -> Counter[str]:
    """Extract countable lexical features from a document."""

    normalized = normalize_text(text)
    features: Counter[str] = Counter()
    for token in TOKEN_PATTERN.findall(normalized):
        features[f"tok:{token}"] += 1
        if len(token) >= 4:
            for index in range(len(token) - 2):
                features[f"tri:{token[index:index + 3]}"] += 1
    return features


def build_rule_feature_set(rule_contents: Iterable[str]) -> set[str]:
    features: set[str] = set()
    for content in rule_contents:
        features.update(content_features(content))
    return features


def parse_ce_date(value: object) -> date | None:
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
    year_text, month_text, day_text = match.groups()
    year = int(year_text)
    month = int(month_text or 1)
    day = int(day_text or 1)
    try:
        return date(year, month, day)
    except ValueError:
        return None


def extract_latest_date(*values: object) -> date | None:
    dates: list[date] = []
    for value in values:
        text = str(value or "")
        parsed = parse_ce_date(text)
        if parsed:
            dates.append(parsed)
        for year_text, month_text, day_text in DATE_PATTERN.findall(text):
            try:
                dates.append(date(int(year_text), int(month_text or 1), int(day_text or 1)))
            except ValueError:
                continue
    return max(dates) if dates else None


def recency_score(post_date: date | None, *, today: date | None = None) -> float:
    if post_date is None:
        return 0.35
    today = today or date.today()
    age_days = max(0, (today - post_date).days)
    age_years = age_days / 365.25
    if age_years <= 0.5:
        return 1.0
    if age_years <= 1:
        return 0.9
    if age_years <= 2:
        return 0.75
    if age_years <= 3:
        return 0.55
    if age_years <= 5:
        return 0.30
    return 0.15


def calculate_ce_priority(
    ce_content: str,
    rule_features: set[str],
    metadata: dict | None = None,
    title: str = "",
    today: date | None = None,
) -> tuple[float, dict[str, float | int | str | None]]:
    """Score CE priority using rule overlap and publication recency."""

    metadata = metadata or {}
    ce_features = content_features(ce_content)
    total = sum(ce_features.values())
    if total <= 0:
        return 0.0, {
            "rule": "ce_rule_overlap_recency",
            "feature_count": 0,
            "matched_feature_count": 0,
            "rule_content_overlap": 0.0,
            "recency_score": 0.0,
            "latest_ce_date": None,
        }

    matched = sum(count for feature, count in ce_features.items() if feature in rule_features)
    overlap = matched / total
    latest_date = extract_latest_date(
        metadata.get("date"),
        metadata.get("doc_title"),
        metadata.get("source_file"),
        title,
    )
    recency = recency_score(latest_date, today=today)
    score = (0.55 * overlap) + (0.45 * recency)
    score = max(0.0, min(1.0, score))
    return score, {
        "rule": "ce_rule_overlap_recency",
        "feature_count": total,
        "matched_feature_count": matched,
        "rule_content_overlap": overlap,
        "recency_score": recency,
        "latest_ce_date": latest_date.isoformat() if latest_date else None,
    }

"""Priority scoring policy for CE source documents."""

from __future__ import annotations

import re
from collections import Counter
from typing import Iterable

TOKEN_PATTERN = re.compile(r"[0-9a-zA-Z\uac00-\ud7a3]+")


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


def calculate_ce_priority(
    ce_content: str,
    rule_features: set[str],
) -> tuple[float, dict[str, float | int | str]]:
    """Score CE priority by the ratio of CE content present in rule content."""

    ce_features = content_features(ce_content)
    total = sum(ce_features.values())
    if total <= 0:
        return 0.0, {
            "rule": "ce_rule_content_overlap",
            "feature_count": 0,
            "matched_feature_count": 0,
            "rule_content_overlap": 0.0,
        }

    matched = sum(count for feature, count in ce_features.items() if feature in rule_features)
    overlap = matched / total
    return overlap, {
        "rule": "ce_rule_content_overlap",
        "feature_count": total,
        "matched_feature_count": matched,
        "rule_content_overlap": overlap,
    }


"""Detect HTTP success responses that actually contain an access-denial page."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from bs4 import BeautifulSoup


ACCESS_BLOCKED = "ACCESS_BLOCKED"
_DENY_PATHS = ("/common/deny.jsp", "/deny.jsp", "/access-denied", "/forbidden")
_DENY_PHRASES = (
    "접근이 거부", "접근 권한이 없습니다", "접근할 수 없습니다", "접근금지",
    "비정상적인 접근", "요청하신 페이지에 접근", "access denied", "request blocked",
)


@dataclass(frozen=True)
class AccessBlockEvidence:
    blocked: bool
    reasons: tuple[str, ...]
    title: str | None


def detect_access_block(*, final_url: str, html: str) -> AccessBlockEvidence:
    soup = BeautifulSoup(html or "", "lxml")
    title = soup.title.get_text(" ", strip=True) if soup.title else None
    body = soup.get_text(" ", strip=True)
    path = urlsplit(final_url).path.lower().rstrip("/") or "/"
    reasons: list[str] = []
    if any(path.endswith(candidate) for candidate in _DENY_PATHS):
        reasons.append("deny_url")
    title_lower, body_lower = (title or "").lower(), body.lower()
    if any(phrase in title_lower for phrase in _DENY_PHRASES):
        reasons.append("deny_title")
    if any(phrase in body_lower for phrase in _DENY_PHRASES):
        reasons.append("deny_body")
    if re.search(r"\b(?:403|forbidden)\b", title_lower) and "access" in body_lower:
        reasons.append("forbidden_page")
    return AccessBlockEvidence(bool(reasons), tuple(dict.fromkeys(reasons)), title)

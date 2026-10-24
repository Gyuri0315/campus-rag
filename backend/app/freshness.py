"""Prefer the current semester's notices, and undergraduate sources.

Recurring notices (수강신청, 등록금, 계절수업, ...) are re-posted every term, so
a plain "수강신청 기간" query matches hundreds of near-identical notices from
2021-2026 and nothing in the ranking preferred the newest one. Measured
2026-10-13: the 2026-2학기 학부 본수강신청 notice sat at keyword rank 34/39
(only 30 were fetched) and the answer quoted a 2025-1학기 *graduate school*
notice instead. Two soft penalties fix that:

- stale: notices (not rules or the student-life pages, whose dates are
  enactment/page dates and stay valid) lose STALE_PENALTY_PER_TERM per
  semester behind the current one, capped at STALE_PENALTY_CAP. Skipped when
  the question names a year itself ("2024년 수강신청").
- schedule questions ("언제", "기간", "일정", "마감", ...) are where the
  current term matters most, so there the stale penalty is doubled
  (SCHEDULE_*), current/next-term notices get CURRENT_TERM_BONUS, and each
  notice table gets SCHEDULE_SEED_SLOTS reserved candidate-pool slots (the
  pool is otherwise dominated by rules/student-life rows, whose fixed
  dataset priorities are higher than any notice's).
- graduate: sources whose title/subcategory is about 대학원 lose
  GRADUATE_PENALTY unless the question is about graduate school.

Both are folded into retrieval's _final_score and, scaled by the reranker
weight, into the post-rerank score, so the total effect is the same with or
without the reranker.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Optional

FRESHNESS_RPCS = frozenset({"match_pknu_notice_documents", "match_rag_documents"})
STALE_PENALTY_PER_TERM = 0.03
STALE_PENALTY_CAP = 0.15
GRADUATE_PENALTY = 0.08
SCHEDULE_STALE_PENALTY_PER_TERM = 0.06
SCHEDULE_STALE_PENALTY_CAP = 0.30
CURRENT_TERM_BONUS = 0.06
SCHEDULE_SEED_SLOTS = 3
# Keyword search for a common term ties hundreds of notices; fetch deeper for
# the notice tables so the current term's notice is in the pool at all.
FRESHNESS_LEXICAL_FETCH = 80

_KST = timezone(timedelta(hours=9))
_TITLE_TERM_RE = re.compile(r"(20\d{2})\s*(?:학년도)?\s*[-.]?\s*(?:제\s*)?([12])\s*학기")
_YEAR_RE = re.compile(r"(?<!\d)(20\d{2})(?!\d)")
_ISO_DATE_RE = re.compile(r"(?<!\d)(20\d{2})-(\d{2})-(\d{2})")
_QUERY_TIME_RE = re.compile(r"(?<!\d)20\d{2}(?!\d)|작년|지난\s*(?:해|학기)|재작년")
_GRADUATE_QUERY_TERMS = ("대학원", "석사", "박사")
_SCHEDULE_QUERY_RE = re.compile(r"언제|기간|일정|마감|날짜|며칠|몇\s*일|까지|개강|종강|시작")


def is_schedule_query(query_text: Optional[str]) -> bool:
    return bool(query_text and _SCHEDULE_QUERY_RE.search(query_text))


def today_kst() -> date:
    return datetime.now(_KST).date()


def term_index(year: int, semester: int) -> int:
    """Semesters as one increasing number: 2026-1 -> 4052, 2026-2 -> 4053."""
    return year * 2 + (semester - 1)


def current_term(today: Optional[date] = None) -> int:
    today = today or today_kst()
    return term_index(today.year, 1 if today.month <= 6 else 2)


def current_term_label(today: Optional[date] = None) -> str:
    today = today or today_kst()
    return f"{today.year}학년도 {1 if today.month <= 6 else 2}학기"


def _row_title(row: Dict[str, Any]) -> str:
    metadata = row.get("metadata") or {}
    return str(metadata.get("doc_title") or row.get("title") or metadata.get("title") or "")


def _date_term(value: Any) -> Optional[int]:
    match = _ISO_DATE_RE.search(str(value or ""))
    if not match:
        return None
    year, month = int(match.group(1)), int(match.group(2))
    if not 1 <= month <= 12:
        return None
    return term_index(year, 1 if month <= 6 else 2)


def document_term(row: Dict[str, Any]) -> Optional[int]:
    """Best guess of which semester a notice belongs to, or None.

    Title "2026-2학기"/"2026학년도 제2학기" wins (it names the term the notice
    is about); then the posting date; then a bare title year (treated as that
    year's 2nd term, so "2026학년도 학사일정" isn't stale during 2026); then a
    date near the top of the body, where crawled notices carry their date.
    """
    title = _row_title(row)
    match = _TITLE_TERM_RE.search(title)
    if match:
        return term_index(int(match.group(1)), int(match.group(2)))
    metadata = row.get("metadata") or {}
    for key in ("published_at", "date", "posted_at"):
        term = _date_term(metadata.get(key))
        if term is not None:
            return term
    match = _YEAR_RE.search(title)
    if match:
        return term_index(int(match.group(1)), 2)
    return _date_term(str(row.get("content") or "")[:300])


def stale_penalty(row: Dict[str, Any], rpc_name: str, query_text: Optional[str], today: Optional[date] = None) -> float:
    if rpc_name not in FRESHNESS_RPCS:
        return 0.0
    if query_text and _QUERY_TIME_RE.search(query_text):
        return 0.0
    term = document_term(row)
    if term is None:
        return 0.0
    behind = current_term(today) - term
    if is_schedule_query(query_text):
        if behind <= 0:
            # This term's or next term's notice: -bonus, i.e. a boost.
            return -CURRENT_TERM_BONUS if behind >= -1 else 0.0
        return min(SCHEDULE_STALE_PENALTY_CAP, behind * SCHEDULE_STALE_PENALTY_PER_TERM)
    if behind <= 0:
        return 0.0
    return min(STALE_PENALTY_CAP, behind * STALE_PENALTY_PER_TERM)


def graduate_penalty(row: Dict[str, Any], query_text: Optional[str]) -> float:
    if query_text and any(term in query_text for term in _GRADUATE_QUERY_TERMS):
        return 0.0
    metadata = row.get("metadata") or {}
    labels = f"{_row_title(row)} {metadata.get('subcategory') or ''}"
    return GRADUATE_PENALTY if "대학원" in labels else 0.0


# Chunk table behind each notice RPC, for pulling a notice's opening chunks.
CHUNK_TABLES = {
    "match_pknu_notice_documents": "pknu_notice_chunks",
    "match_rag_documents": "rag_chunks",
}
# On a schedule question, the opening chunks of the OPENING_SOURCES
# best-scoring current-term notices (OPENING_CHUNKS each) are added to the reranker's
# candidates. Notices put their key dates up front, but keyword search ranks
# whichever chunk repeats the query word most: for "이번 학기 수강신청 기간",
# the 2026-2학기 본수강신청 notice made the pool only through its chunk #4
# (학점 이월 rules, "수강신청" x15) while chunks #0-#2 hold every date, and
# the answer was "관련 정보를 찾을 수 없습니다" (measured 2026-10-14).
OPENING_SOURCES = 3
OPENING_CHUNKS = 3


def is_current_term_notice(row: Dict[str, Any], today: Optional[date] = None) -> bool:
    term = document_term(row)
    return term is not None and term >= current_term(today)


def seed_slots(rpc_name: str, query_text: Optional[str]) -> int:
    """How many top rows of this RPC to reserve in the candidate pool."""
    if rpc_name in FRESHNESS_RPCS and is_schedule_query(query_text):
        return SCHEDULE_SEED_SLOTS
    return 1


def freshness_adjustment(row: Dict[str, Any], rpc_name: str, query_text: Optional[str], today: Optional[date] = None) -> float:
    """Score adjustment for one candidate row (positive only for a
    current-term notice on a schedule question)."""
    return -(stale_penalty(row, rpc_name, query_text, today) + graduate_penalty(row, query_text))

"""Popular questions for the home page and the empty chat screen.

Public (no login): the home page is what logged-out visitors see. Because
other people's questions are shown to anyone, a question only qualifies once
MIN_DISTINCT_USERS different users have asked it -- a one-off question that
might carry someone's personal details never appears. The result is cached
for CACHE_SECONDS so page views don't each scan chat_messages.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict
from typing import Any, Dict, Iterable, List, Optional, Tuple

from fastapi import APIRouter, Depends

from ..deps import AppState, get_state
from .admin import normalize_question

logger = logging.getLogger(__name__)
router = APIRouter()

QUESTION_SAMPLE_LIMIT = 2000
MIN_DISTINCT_USERS = 3
POPULAR_LIMIT = 5
CACHE_SECONDS = 600
MAX_QUESTION_CHARS = 60

_cache_lock = threading.Lock()
_cache: Optional[Tuple[float, List[Dict[str, Any]]]] = None


def rank_popular(rows: Iterable[Dict[str, Any]], limit: int = POPULAR_LIMIT) -> List[Dict[str, Any]]:
    """rows: {"content", "user_id"}. Most-asked questions with at least
    MIN_DISTINCT_USERS distinct askers, ranked by that user count."""
    users: Dict[str, set] = defaultdict(set)
    display: Dict[str, str] = {}
    for row in rows:
        text = str(row.get("content") or "").strip()
        user_id = row.get("user_id")
        if not text or not user_id or len(text) > MAX_QUESTION_CHARS:
            continue
        key = normalize_question(text)
        users[key].add(user_id)
        display.setdefault(key, text)
    qualified = [(key, len(askers)) for key, askers in users.items() if len(askers) >= MIN_DISTINCT_USERS]
    qualified.sort(key=lambda item: item[1], reverse=True)
    return [{"question": display[key], "askers": count} for key, count in qualified[:limit]]


def _fetch_rows(state: AppState) -> List[Dict[str, Any]]:
    data = (
        state.supabase.table("chat_messages")
        .select("content, chats(user_id)")
        .eq("role", "user")
        .order("created_at", desc=True)
        .limit(QUESTION_SAMPLE_LIMIT)
        .execute()
        .data
        or []
    )
    return [
        {"content": row.get("content"), "user_id": (row.get("chats") or {}).get("user_id")}
        for row in data
    ]


@router.get("/popular-questions")
def popular_questions(state: AppState = Depends(get_state)) -> Dict[str, Any]:
    """Empty list when nothing qualifies yet; the UI falls back to its own
    default questions then."""
    global _cache
    with _cache_lock:
        if _cache and time.monotonic() - _cache[0] < CACHE_SECONDS:
            return {"questions": _cache[1]}
    try:
        questions = rank_popular(_fetch_rows(state))
    except Exception:
        logger.exception("popular questions query failed")
        return {"questions": []}
    with _cache_lock:
        _cache = (time.monotonic(), questions)
    return {"questions": questions}

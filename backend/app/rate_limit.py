"""Per-user rate limiting for /ask.

A small hand-rolled sliding-window limiter, not slowapi: slowapi's
`@limiter.limit()` decorator resolves the endpoint's type hints using the
*wrapper* function's `__globals__` (the slowapi module's, not app.routers.ask's),
which breaks FastAPI's body-vs-query inference whenever the route module uses
`from __future__ import annotations` (postponed evaluation) — as this codebase
does everywhere. A plain dependency avoids that whole bug class.

Single Render instance (see backend/render.yaml, plan: free) so in-memory
state is fine — no Redis needed.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

from fastapi import Depends, HTTPException

from .deps import require_user

MAX_REQUESTS_PER_WINDOW = 20
WINDOW_SECONDS = 60.0

_lock = threading.Lock()
_hits: dict[str, deque[float]] = defaultdict(deque)


def _check(key: str, *, limit: int, window_seconds: float) -> None:
    now = time.monotonic()
    with _lock:
        hits = _hits[key]
        while hits and now - hits[0] > window_seconds:
            hits.popleft()
        if len(hits) >= limit:
            raise HTTPException(
                status_code=429,
                detail="요청이 너무 많습니다. 잠시 후 다시 시도해 주세요.",
            )
        hits.append(now)


def enforce_ask_rate_limit(user_id: str = Depends(require_user)) -> str:
    """FastAPI dependency: verifies login (via require_user) and rate-limits
    the verified user id. Returning user_id lets the route reuse it without
    a second Depends(require_user) call (FastAPI dependency caching would
    dedupe that anyway, but this keeps the route's dependency list simple).
    """
    _check(f"user:{user_id}", limit=MAX_REQUESTS_PER_WINDOW, window_seconds=WINDOW_SECONDS)
    return user_id

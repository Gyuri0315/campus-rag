"""A Supabase client that is safe to share across request threads.

FastAPI runs sync endpoints and dependencies (require_user's auth check,
/ask, /admin) on a thread pool, and they all used one Supabase client. Its
HTTP/2 connection is not thread-safe: measured 2026-10-14 with 4-16 threads
doing plain reads, 14-19 of 80 calls failed (WinError 10035 socket errors,
h2 StreamIDTooLowError, ConnectionTerminated) versus 0 of 80 single-threaded.
For users that means random "로그인 세션이 유효하지 않습니다" or failed
answers whenever several people ask at the same time.

ThreadLocalSupabase looks like a Client but hands each thread its own,
created on first use and reused by that thread afterwards.
"""

from __future__ import annotations

import threading
from typing import Any, Callable

from supabase import Client, create_client


class ThreadLocalSupabase:
    def __init__(self, url: str, key: str, factory: Callable[[str, str], Client] = create_client) -> None:
        self.supabase_url = url
        self.supabase_key = key
        self._factory = factory
        self._local = threading.local()

    def _client(self) -> Client:
        client = getattr(self._local, "client", None)
        if client is None:
            client = self._local.client = self._factory(self.supabase_url, self.supabase_key)
        return client

    def __getattr__(self, name: str) -> Any:
        # Only called for attributes not set in __init__ (table, rpc, auth, ...).
        return getattr(self._client(), name)

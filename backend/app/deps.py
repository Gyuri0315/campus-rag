"""Shared application state and FastAPI dependency providers."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, Request
from openai import OpenAI
from supabase import Client

from .config import Settings
from .embeddings import Embedder
from .reranking import CrossEncoderReranker

logger = logging.getLogger(__name__)


@dataclass
class AppState:
    settings: Settings
    embedder: Embedder
    reranker: CrossEncoderReranker | None
    supabase: Client
    openai: OpenAI
    system_prompt: str


def get_state(request: Request) -> AppState:
    """FastAPI dependency that returns the shared AppState built in lifespan."""
    state: AppState = request.app.state.app_state
    return state


def require_user(
    request: Request,
    authorization: str | None = Header(default=None),
    state: AppState = Depends(get_state),
) -> str:
    """Verify the caller's Supabase session and return their user id.

    Login is mandatory for /ask so a school-email-only account (see the
    signup domain trigger) is also what gets rate-limited. Verifying
    against Supabase (rather than decoding the JWT locally) also means a
    revoked/expired session is rejected immediately without needing to
    manage a separate JWT secret here.
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="로그인이 필요합니다.")
    token = authorization.split(" ", 1)[1].strip()
    if not token:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다.")

    try:
        response = state.supabase.auth.get_user(token)
    except Exception:
        logger.info("auth: token verification failed", exc_info=True)
        raise HTTPException(status_code=401, detail="로그인 세션이 유효하지 않습니다.")

    if not response or not response.user:
        raise HTTPException(status_code=401, detail="로그인 세션이 유효하지 않습니다.")

    request.state.user_id = response.user.id
    request.state.user_email = (response.user.email or "").lower()
    return response.user.id


def require_admin(
    request: Request,
    user_id: str = Depends(require_user),
    state: AppState = Depends(get_state),
) -> str:
    """Logged-in user whose email is in Settings.admin_emails, else 403.

    Admin endpoints read every user's chats/feedback with the service-role
    client (RLS bypassed), so the allowlist is the only gate. An empty list
    denies everyone.
    """
    allowed = {email.lower() for email in state.settings.admin_emails}
    if getattr(request.state, "user_email", "") not in allowed:
        raise HTTPException(status_code=403, detail="관리자만 접근할 수 있습니다.")
    return user_id

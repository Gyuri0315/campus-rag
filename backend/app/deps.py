"""Shared application state and FastAPI dependency providers."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Request
from openai import OpenAI
from supabase import Client

from .config import Settings
from .embeddings import Embedder


@dataclass
class AppState:
    settings: Settings
    embedder: Embedder
    supabase: Client
    openai: OpenAI
    system_prompt: str


def get_state(request: Request) -> AppState:
    """FastAPI dependency that returns the shared AppState built in lifespan."""
    state: AppState = request.app.state.app_state
    return state

"""FastAPI application factory and lifespan."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from openai import OpenAI

from .config import get_settings
from .deps import AppState
from .embeddings import Embedder
from .generation import load_system_prompt
from .retrieval import build_supabase_client
from .routers import ask as ask_router

logger = logging.getLogger(__name__)


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    _configure_logging(settings.log_level)
    logger.info("Backend starting up (model=%s)…", settings.embedding_model)

    embedder = Embedder(
        model_name=settings.embedding_model,
        device=settings.embedding_device,
        expected_dimensions=settings.expected_dimensions,
    )
    supabase = build_supabase_client(
        settings.supabase_url, settings.supabase_service_role_key
    )
    openai_client = OpenAI(
        api_key=settings.openai_api_key,
        timeout=settings.openai_timeout_seconds,
    )
    system_prompt = load_system_prompt()

    app.state.app_state = AppState(
        settings=settings,
        embedder=embedder,
        supabase=supabase,
        openai=openai_client,
        system_prompt=system_prompt,
    )
    logger.info("Backend startup complete.")

    try:
        yield
    finally:
        logger.info("Backend shutting down.")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Campus RAG Backend",
        description="RAG question-answering API for campus-rag.",
        version="0.1.0",
        lifespan=lifespan,
    )

    cors_origins = list(settings.cors_origins)
    for origin in (
        "http://localhost:3000",
        "https://campus-rag.vercel.app",
    ):
        if origin not in cors_origins:
            cors_origins.append(origin)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    app.include_router(ask_router.router)
    return app


app = create_app()

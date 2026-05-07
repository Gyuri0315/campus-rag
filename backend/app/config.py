"""Application settings loaded from environment variables / .env."""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Annotated, List

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Supabase
    supabase_url: str
    supabase_service_role_key: str

    # OpenAI
    openai_api_key: str
    openai_model: str = "gpt-4o-mini"
    openai_timeout_seconds: float = 30.0

    # Embedding (dimension MUST match rag_chunks.embedding column type)
    embedding_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    embedding_device: str = "cpu"
    expected_dimensions: int = 384

    # Retrieval
    rag_top_k: int = 5
    rag_min_similarity: float = 0.35
    rpc_name: str = "match_rag_documents"
    max_chars_per_chunk: int = 500

    # Server
    # NoDecode disables pydantic-settings' default JSON-decode for complex types,
    # so a plain "a,b,c" string from .env reaches our validator instead of
    # blowing up in json.loads. JSON list form (["a","b"]) is still accepted
    # by the validator below.
    cors_origins: Annotated[List[str], NoDecode] = ["http://localhost:3000"]
    log_level: str = "INFO"
    port: int = 8000

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_csv(cls, value):
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return []
            if stripped.startswith("["):
                try:
                    parsed = json.loads(stripped)
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        f"CORS_ORIGINS looks like JSON but failed to parse: {exc}"
                    ) from exc
                if not isinstance(parsed, list):
                    raise ValueError("CORS_ORIGINS JSON must be an array of strings")
                return [str(item).strip() for item in parsed if str(item).strip()]
            return [item.strip() for item in stripped.split(",") if item.strip()]
        return value


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]

"""Application settings loaded from environment variables / .env."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Annotated, List

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parent.parent / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Supabase
    supabase_url: str = Field(validation_alias=AliasChoices("SUPABASE_URL"))
    supabase_service_role_key: str

    # OpenAI
    openai_api_key: str
    openai_model: str = "gpt-4o-mini"
    openai_timeout_seconds: float = 30.0
    openai_temperature: float = 0.1
    openai_max_tokens: int = 700

    # Embedding (dimension MUST match rag_chunks.embedding column type)
    embedding_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    embedding_device: str = "cpu"
    expected_dimensions: int = 384

    # Retrieval
    rag_top_k: int = 10
    rag_first_stage_k: int = 30
    rag_min_similarity: float = 0.30
    rag_max_chunks_per_url: int = 2
    rag_priority_weight: float = 0.30
    rag_dataset_priority_weight: float = 0.15
    rag_source_kind_weight: float = 0.10
    reranker_enabled: bool = True
    reranker_model: str = "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"
    reranker_device: str = "cpu"
    reranker_weight: float = 0.80
    rpc_names: Annotated[List[str], NoDecode] = [
        "match_rule_documents",
        "match_pknu_notice_documents",
        "match_pknu_student_life_documents",
        "match_rag_documents",
    ]
    max_chars_per_chunk: int = 500
    source_excerpt_max_chars: int = Field(default=320, ge=80, le=2000)

    # Server
    # NoDecode disables pydantic-settings' default JSON-decode for complex types,
    # so a plain "a,b,c" string from .env reaches our validator instead of
    # blowing up in json.loads. JSON list form (["a","b"]) is still accepted
    # by the validator below.
    cors_origins: Annotated[List[str], NoDecode] = ["http://localhost:3000"]
    log_level: str = "INFO"
    port: int = 8000

    @field_validator("supabase_url")
    @classmethod
    def _validate_supabase_api_url(cls, value: str):
        if value.startswith(("postgres://", "postgresql://")):
            raise ValueError(
                "SUPABASE_URL must be the Supabase API URL "
                "(https://<project-ref>.supabase.co). Put PostgreSQL connection "
                "strings in DATABASE_URL instead."
            )
        return value

    @field_validator("cors_origins", "rpc_names", mode="before")
    @classmethod
    def _split_csv(cls, value, info):
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return []
            field_label = info.field_name.upper()
            if stripped.startswith("["):
                try:
                    parsed = json.loads(stripped)
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        f"{field_label} looks like JSON but failed to parse: {exc}"
                    ) from exc
                if not isinstance(parsed, list):
                    raise ValueError(f"{field_label} JSON must be an array of strings")
                return [str(item).strip() for item in parsed if str(item).strip()]
            return [item.strip() for item in stripped.split(",") if item.strip()]
        return value


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]

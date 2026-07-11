"""Request/response Pydantic models for the public API."""

from __future__ import annotations

from typing import List

from pydantic import BaseModel, Field


class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)


class Source(BaseModel):
    title: str
    uri: str
    content: str
    similarity: float
    priority_score: float = 0.0
    dataset_priority: float = 0.0
    final_score: float = 0.0
    rerank_score: float = 0.0
    rerank_final_score: float = 0.0


class AskResponse(BaseModel):
    answer: str
    sources: List[Source]

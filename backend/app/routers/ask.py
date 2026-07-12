"""POST /ask RAG question answering endpoint."""

from __future__ import annotations

import logging
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException

from ..deps import AppState, get_state
from ..generation import generate_answer
from ..query_transform import transform_query
from ..retrieval import search
from ..schemas import AskRequest, AskResponse, Source

logger = logging.getLogger(__name__)
router = APIRouter()

NO_INFO_ANSWER = "관련 정보를 찾을 수 없습니다."


def _similarity(row: Dict[str, Any]) -> float:
    try:
        return float(row.get("similarity") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _score(row: Dict[str, Any], key: str) -> float:
    metadata = row.get("metadata") or {}
    try:
        return float(row.get(key, metadata.get(key)) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _row_to_source(row: Dict[str, Any]) -> Source:
    metadata = row.get("metadata") or {}
    title = (
        metadata.get("doc_title")
        or metadata.get("source_file")
        or metadata.get("title")
        or row.get("title")
        or row.get("source_slug")
        or "(제목 없음)"
    )
    uri = (
        row.get("uri")
        or row.get("url")
        or metadata.get("doc_url")
        or metadata.get("source_page_url")
        or metadata.get("attachment_url")
        or ""
    )
    return Source(
        title=str(title),
        uri=str(uri),
        content=str(row.get("content") or ""),
        similarity=float(row.get("similarity") or 0.0),
        priority_score=_score(row, "priority_score"),
        dataset_priority=_score(row, "dataset_priority"),
        final_score=_score(row, "final_score"),
        rerank_score=_score(row, "rerank_score"),
        rerank_final_score=_score(row, "rerank_final_score"),
    )


@router.post("/ask", response_model=AskResponse)
def ask(payload: AskRequest, state: AppState = Depends(get_state)) -> AskResponse:
    question = payload.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="question must not be empty")

    search_query = transform_query(question) or question
    logger.info("ask: question=%r search=%r", question, search_query)

    try:
        embedding = state.embedder.encode_query(search_query)
    except Exception:
        logger.exception("embedding failed")
        raise HTTPException(status_code=500, detail="embedding failed")

    try:
        rows = search(
            state.supabase,
            rpc_names=state.settings.rpc_names,
            embedding=embedding,
            top_k=state.settings.rag_top_k,
            first_stage_k=state.settings.rag_first_stage_k,
            min_similarity=state.settings.rag_min_similarity,
            priority_weight=state.settings.rag_priority_weight,
            dataset_priority_weight=state.settings.rag_dataset_priority_weight,
            source_kind_weight=state.settings.rag_source_kind_weight,
            reranker=state.reranker,
            reranker_weight=state.settings.reranker_weight,
            query_text=search_query,
        )
    except Exception:
        logger.exception("retrieval failed")
        raise HTTPException(status_code=502, detail="retrieval failed")

    sources = [_row_to_source(r) for r in rows]
    top_similarity = max((_similarity(row) for row in rows), default=0.0)
    logger.info(
        "ask: final_sources=%d top_similarity=%.4f",
        len(sources),
        top_similarity,
    )
    if logger.isEnabledFor(logging.DEBUG):
        source_summaries = [
            {
                "index": index,
                "rpc": (row.get("metadata") or {}).get("rpc_name"),
                "title": source.title[:120],
                "similarity": round(source.similarity, 4),
                "priority_score": round(source.priority_score, 4),
                "dataset_priority": round(source.dataset_priority, 4),
                "final_score": round(source.final_score, 4),
                "rerank_score": round(source.rerank_score, 4),
                "rerank_final_score": round(source.rerank_final_score, 4),
            }
            for index, (row, source) in enumerate(zip(rows, sources), start=1)
        ]
        logger.debug("ask: final_source_summaries=%s", source_summaries)

    if not sources:
        return AskResponse(answer=NO_INFO_ANSWER, sources=[])

    try:
        answer = generate_answer(
            openai_client=state.openai,
            model=state.settings.openai_model,
            system_prompt=state.system_prompt,
            question=question,
            rows=rows,
            max_chars_per_chunk=state.settings.max_chars_per_chunk,
            timeout=state.settings.openai_timeout_seconds,
            temperature=state.settings.openai_temperature,
            max_tokens=state.settings.openai_max_tokens,
        )
    except Exception:
        logger.exception("generation failed")
        raise HTTPException(status_code=502, detail="generation failed")

    return AskResponse(answer=answer or NO_INFO_ANSWER, sources=sources)

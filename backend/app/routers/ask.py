"""POST /ask — RAG question answering endpoint."""

from __future__ import annotations

import logging
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException

from ..deps import AppState, get_state
from ..generation import generate_answer
from ..retrieval import search
from ..schemas import AskRequest, AskResponse, Source

logger = logging.getLogger(__name__)
router = APIRouter()

NO_INFO_ANSWER = "관련 정보를 찾을 수 없습니다."


def _row_to_source(row: Dict[str, Any]) -> Source:
    metadata = row.get("metadata") or {}
    title = (
        metadata.get("doc_title")
        or metadata.get("source_file")
        or metadata.get("title")
        or row.get("source_slug")
        or "(제목 없음)"
    )
    uri = (
        row.get("uri")
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
    )


@router.post("/ask", response_model=AskResponse)
def ask(payload: AskRequest, state: AppState = Depends(get_state)) -> AskResponse:
    question = payload.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="question must not be empty")

    logger.info("ask: question=%s", question)

    try:
        embedding = state.embedder.encode_query(question)
    except Exception:
        logger.exception("embedding failed")
        raise HTTPException(status_code=500, detail="embedding failed")

    try:
        rows = search(
            state.supabase,
            rpc_name=state.settings.rpc_name,
            embedding=embedding,
            top_k=state.settings.rag_top_k,
            min_similarity=state.settings.rag_min_similarity,
        )
    except Exception:
        logger.exception("retrieval failed")
        raise HTTPException(status_code=502, detail="retrieval failed")

    sources = [_row_to_source(r) for r in rows]

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
        )
    except Exception:
        logger.exception("generation failed")
        raise HTTPException(status_code=502, detail="generation failed")

    return AskResponse(answer=answer or NO_INFO_ANSWER, sources=sources)

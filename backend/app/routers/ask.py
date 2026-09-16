"""POST /ask RAG question answering endpoint."""

from __future__ import annotations

import json
import logging
from itertools import zip_longest
from typing import Any, Dict, Iterator, List, Tuple
from urllib.parse import unquote, urlparse

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from ..deps import AppState, get_state
from ..excerpts import extract_relevant_excerpt
from ..generation import generate_answer, stream_answer
from ..query_rewrite import plan_search_queries
from ..query_transform import transform_query
from ..rate_limit import enforce_ask_rate_limit
from ..retrieval import _dedupe_key, search
from ..schemas import AskRequest, AskResponse, Attachment, Source

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


def _attachment_name(name: Any, url: str, index: int) -> str:
    normalized = str(name or "").strip()
    if normalized:
        return normalized
    filename = unquote(urlparse(url).path.rsplit("/", 1)[-1]).strip()
    return filename or f"attachment-{index}"


def _row_attachments(row: Dict[str, Any]) -> list[Attachment]:
    metadata = row.get("metadata") or {}
    candidates: list[tuple[Any, Any]] = []
    raw_attachments = metadata.get("attachments")
    if isinstance(raw_attachments, list):
        for item in raw_attachments:
            if isinstance(item, dict):
                candidates.append((item.get("name"), item.get("url")))

    candidates.append(
        (metadata.get("attachment_name"), metadata.get("attachment_url"))
    )

    attachments: list[Attachment] = []
    seen_urls: set[str] = set()
    for name, raw_url in candidates:
        url = str(raw_url or "").strip()
        if not url.startswith(("http://", "https://")) or url in seen_urls:
            continue
        seen_urls.add(url)
        attachments.append(
            Attachment(
                name=_attachment_name(name, url, len(attachments) + 1),
                url=url,
            )
        )
    return attachments


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
        attachments=_row_attachments(row),
    )


def _search_one(state: AppState, search_query: str) -> List[Dict[str, Any]]:
    """One embed+search() call for a single (already standalone) query
    string -- the same call _retrieve() always made, just factored out so
    it can run once per decomposed sub-query."""
    embedding = state.embedder.encode_query(search_query)
    return search(
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
        max_chunks_per_url=state.settings.rag_max_chunks_per_url,
        max_lexical_chunks_per_url=state.settings.rag_max_lexical_chunks_per_url,
        query_text=search_query,
    )


def _merge_rows(rows_by_query: List[List[Dict[str, Any]]], top_k: int) -> List[Dict[str, Any]]:
    """Round-robin merge of one already-ranked rows list per sub-query.

    Interleaving (rather than a global sort by final_score) matters
    specifically for compound queries: each sub-query's final_score was
    computed against its own reranker pass, on its own scale, so a plain
    global sort would tend to let whichever intent scores systematically
    higher crowd out the other -- exactly the failure mode decomposition
    was meant to fix. Round-robin guarantees every sub-query gets a fair
    share of the final slots. A single-query call (the common case) is
    just one list, so this is a no-op pass-through.
    """
    merged: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for row_group in zip_longest(*rows_by_query):
        for row in row_group:
            if row is None:
                continue
            key = _dedupe_key(row)
            if key in seen:
                continue
            seen.add(key)
            merged.append(row)
            if len(merged) >= top_k:
                return merged
    return merged


def _retrieve(
    *,
    state: AppState,
    user_id: str,
    question: str,
    chat_history: List[Dict[str, str]],
) -> Tuple[List[Dict[str, Any]], List[Source]]:
    """Shared embedding+search step for both the plain and streaming paths.

    `question` alone is frequently not enough to search with well:
    - with prior conversation, a follow-up like "그럼 거기 대표 전화번호는요?"
      carries no literal keyword for what "거기" refers to;
    - a compound question ("도서관 전화번호나 증명서 발급기 위치") packs two
      intents into one sentence that a single embedding/lexical search
      represents as one diluted vector, typically favoring whichever intent
      dominates;
    - a colloquial abbreviation ("컴공") may not literally appear anywhere
      near the formal name ("컴퓨터·인공지능공학부") official documents use.
    plan_search_queries() resolves all three *only for the search step* --
    the original `question` and chat_history are still what gets sent to
    answer generation unchanged (see ask()/generate_answer/stream_answer
    below), so none of this touches generation or the streaming response.
    """
    sub_questions = plan_search_queries(
        openai_client=state.openai,
        model=state.settings.openai_model,
        question=question,
        chat_history=chat_history,
    )
    search_queries = [transform_query(q) or q for q in sub_questions]
    logger.info(
        "ask: user=%s question=%r sub_questions=%r search_queries=%r history_turns=%d",
        user_id,
        question,
        sub_questions,
        search_queries,
        len(chat_history),
    )

    try:
        rows_by_query = [_search_one(state, q) for q in search_queries]
    except Exception:
        logger.exception("retrieval failed")
        raise HTTPException(status_code=502, detail="retrieval failed")

    rows = _merge_rows(rows_by_query, state.settings.rag_top_k)

    sources = [_row_to_source(r) for r in rows]
    top_similarity = max((_similarity(row) for row in rows), default=0.0)
    logger.info("ask: final_sources=%d top_similarity=%.4f", len(sources), top_similarity)
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
    return rows, sources


def _sse(event_type: str, **payload: Any) -> str:
    return f"data: {json.dumps({'type': event_type, **payload}, ensure_ascii=False)}\n\n"


def _stream_ask(
    *,
    state: AppState,
    question: str,
    rows: List[Dict[str, Any]],
    sources: List[Source],
    chat_history: List[Dict[str, str]],
) -> Iterator[str]:
    """text/event-stream body: token-by-token answer, then sources (with
    excerpts trimmed against the now-complete answer), then done/error."""
    if not sources:
        yield _sse("token", content=NO_INFO_ANSWER)
        yield _sse("sources", sources=[])
        yield _sse("done")
        return

    pieces: List[str] = []
    try:
        for delta in stream_answer(
            openai_client=state.openai,
            model=state.settings.openai_model,
            system_prompt=state.system_prompt,
            question=question,
            rows=rows,
            max_chars_per_chunk=state.settings.max_chars_per_chunk,
            timeout=state.settings.openai_timeout_seconds,
            temperature=state.settings.openai_temperature,
            max_tokens=state.settings.openai_max_tokens,
            chat_history=chat_history,
        ):
            pieces.append(delta)
            yield _sse("token", content=delta)
    except Exception:
        logger.exception("generation failed")
        yield _sse("error", detail="generation failed")
        return

    answer = "".join(pieces).strip() or NO_INFO_ANSWER
    excerpted_sources = [
        source.model_copy(
            update={
                "content": extract_relevant_excerpt(
                    source.content,
                    question=question,
                    answer=answer,
                    source_index=index,
                    max_chars=state.settings.source_excerpt_max_chars,
                )
            }
        )
        for index, source in enumerate(sources, start=1)
    ]
    yield _sse("sources", sources=[s.model_dump() for s in excerpted_sources])
    yield _sse("done")


@router.post("/ask")
def ask(
    payload: AskRequest,
    state: AppState = Depends(get_state),
    user_id: str = Depends(enforce_ask_rate_limit),
):
    question = payload.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="question must not be empty")
    chat_history = [turn.model_dump() for turn in payload.chat_history]

    rows, sources = _retrieve(
        state=state,
        user_id=user_id,
        question=question,
        chat_history=chat_history,
    )

    if payload.stream:
        return StreamingResponse(
            _stream_ask(
                state=state,
                question=question,
                rows=rows,
                sources=sources,
                chat_history=chat_history,
            ),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

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
            chat_history=chat_history,
        )
    except Exception:
        logger.exception("generation failed")
        raise HTTPException(status_code=502, detail="generation failed")

    sources = [
        source.model_copy(
            update={
                "content": extract_relevant_excerpt(
                    source.content,
                    question=question,
                    answer=answer,
                    source_index=index,
                    max_chars=state.settings.source_excerpt_max_chars,
                )
            }
        )
        for index, source in enumerate(sources, start=1)
    ]

    return AskResponse(answer=answer or NO_INFO_ANSWER, sources=sources)

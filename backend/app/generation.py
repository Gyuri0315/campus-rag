"""OpenAI prompt assembly and answer generation."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Dict, List

from openai import OpenAI

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


def load_system_prompt() -> str:
    return (PROMPTS_DIR / "system.txt").read_text(encoding="utf-8")


def _truncate(text: str, max_chars: int) -> str:
    if max_chars <= 0:
        return ""
    if len(text) <= max_chars:
        return text
    return text[: max(0, max_chars - 1)].rstrip() + "…"


def _format_similarity(value: Any) -> str:
    if value is None:
        return "유사도 없음"
    try:
        return f"{float(value):.4f}"
    except (TypeError, ValueError):
        return str(value).strip() or "유사도 없음"


def _format_score(value: Any) -> str:
    try:
        return f"{float(value):.4f}"
    except (TypeError, ValueError):
        return "0.0000"


def _format_source_block(rows: List[Dict[str, Any]], max_chars_per_chunk: int) -> str:
    lines: List[str] = []
    for index, row in enumerate(rows, start=1):
        metadata = row.get("metadata") or {}
        title = (
            metadata.get("doc_title")
            or metadata.get("source_file")
            or metadata.get("title")
            or row.get("title")
            or row.get("source_slug")
            or "제목 없음"
        )
        uri = (
            row.get("uri")
            or row.get("url")
            or metadata.get("doc_url")
            or metadata.get("source_page_url")
            or metadata.get("attachment_url")
            or "URL 없음"
        )
        similarity = row.get("similarity")
        similarity_text = _format_similarity(similarity)
        priority_text = _format_score(row.get("priority_score"))
        dataset_priority_text = _format_score(row.get("dataset_priority"))
        final_score_text = _format_score(row.get("final_score"))
        rerank_score_text = _format_score(row.get("rerank_score"))
        content = _truncate(str(row.get("content") or ""), max_chars_per_chunk)
        lines.append(f"[{index}]")
        lines.append(f"제목: {str(title).strip() or '제목 없음'}")
        lines.append(f"URL: {str(uri).strip() or 'URL 없음'}")
        lines.append(f"유사도: {similarity_text}")
        lines.append(f"우선순위: {priority_text}")
        lines.append(f"데이터셋우선순위: {dataset_priority_text}")
        lines.append(f"최종점수: {final_score_text}")
        lines.append(f"재정렬점수: {rerank_score_text}")
        lines.append("본문:")
        lines.append(content or "(본문 없음)")
        lines.append("")
    return "\n".join(lines).rstrip()


def build_user_message(
    question: str,
    rows: List[Dict[str, Any]],
    max_chars_per_chunk: int,
) -> str:
    sources_block = (
        _format_source_block(rows, max_chars_per_chunk) if rows else "(자료 없음)"
    )
    return (
        "## 사용자 질문\n"
        f"{question}\n\n"
        "## 참고 자료\n"
        f"{sources_block}\n\n"
        "## 답변 지시\n"
        "- 참고 자료에 있는 내용만 근거로 한국어로 답변하세요.\n"
        "- 답변에 사용한 근거는 문장 끝에 [1], [2] 형식으로 표시하세요.\n"
        "- 참고 자료만으로 답할 수 없으면 \"관련 정보를 찾을 수 없습니다.\"라고만 답하세요."
    )


def generate_answer(
    *,
    openai_client: OpenAI,
    model: str,
    system_prompt: str,
    question: str,
    rows: List[Dict[str, Any]],
    max_chars_per_chunk: int,
    timeout: float,
    temperature: float = 0.1,
    max_tokens: int = 700,
) -> str:
    user_message = build_user_message(question, rows, max_chars_per_chunk)
    logger.info(
        "generation: start model=%s source_count=%d max_chars_per_chunk=%d",
        model,
        len(rows),
        max_chars_per_chunk,
    )
    started = time.perf_counter()
    response = openai_client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
    )
    elapsed_ms = (time.perf_counter() - started) * 1000
    usage = getattr(response, "usage", None)
    prompt_tokens = getattr(usage, "prompt_tokens", None) if usage else None
    completion_tokens = getattr(usage, "completion_tokens", None) if usage else None
    total_tokens = getattr(usage, "total_tokens", None) if usage else None
    logger.info(
        "generation: done model=%s latency_ms=%.1f prompt_tokens=%s completion_tokens=%s total_tokens=%s",
        model,
        elapsed_ms,
        prompt_tokens,
        completion_tokens,
        total_tokens,
    )
    return (response.choices[0].message.content or "").strip()

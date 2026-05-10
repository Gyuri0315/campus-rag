"""OpenAI prompt assembly and answer generation."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List

from openai import OpenAI

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


def load_system_prompt() -> str:
    return (PROMPTS_DIR / "system.txt").read_text(encoding="utf-8")


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max(0, max_chars - 1)].rstrip() + "…"


def _format_source_block(rows: List[Dict[str, Any]], max_chars_per_chunk: int) -> str:
    lines: List[str] = []
    for index, row in enumerate(rows, start=1):
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
        content = _truncate(str(row.get("content") or ""), max_chars_per_chunk)
        header = f"[{index}] {title}"
        if uri:
            header += f"\nURL: {uri}"
        lines.append(header)
        lines.append(content)
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
        "[질문]\n"
        f"{question}\n\n"
        "[참고 자료]\n"
        f"{sources_block}\n\n"
        "[지시]\n"
        "위 자료만 근거로, 시스템 규칙을 지켜 한국어로 답변하세요."
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
) -> str:
    user_message = build_user_message(question, rows, max_chars_per_chunk)
    response = openai_client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        temperature=0.2,
        timeout=timeout,
    )
    return (response.choices[0].message.content or "").strip()

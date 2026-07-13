"""Supabase RPC search wrapper."""

from __future__ import annotations

import hashlib
import logging
import re
import time
from typing import Any, Dict, List, Optional, Protocol

from supabase import Client, create_client

from .reranking import normalize_scores

logger = logging.getLogger(__name__)

ACADEMIC_KEYWORDS = (
    "학사",
    "수강",
    "졸업",
    "전공",
    "복수전공",
    "부전공",
    "전과",
    "성적",
    "학점",
    "장학",
    "휴학",
    "복학",
    "등록",
    "신청",
    "기간",
    "자격",
    "조건",
    "이수",
    "교직",
    "현장실습",
)

NOISE_PHRASES = (
    "개인정보 수집",
    "저작물 활용 동의서",
    "접수번호는 공란",
    "글자크기",
    "연구보고",
    "contents",
    "목 차",
)

GENERIC_STUB_TITLES = (
    "학부 소개",
    "학부소개",
    "졸업 후 진로",
    "졸업후진로",
    "교육과정",
    "국립 부경대학교 대학생활 E-하나로",
    "대학생활 E-하나로",
)

_REPEATED_CHAR_RE = re.compile(r"(.)\1{5,}")
_STANDALONE_JAMO_RE = re.compile(r"[ㄱ-ㅎㅏ-ㅣ]{6,}")

QUERY_TERM_GROUPS = {
    "복수전공": ("복수전공", "복수 전공", "다전공", "전공제도"),
    "부전공": ("부전공", "부 전공", "다전공", "전공제도"),
    "전과": ("전과", "전부", "전공변경"),
    "전공": ("전공", "전공제도"),
    "졸업": ("졸업", "졸업요건", "학위수여"),
    "학점": ("학점", "소요학점", "이수학점"),
    "수강신청": ("수강신청", "수강 신청"),
    "휴학": ("휴학",),
    "복학": ("복학",),
    "장학": ("장학", "장학금"),
    "등록금": ("등록금", "등록"),
    "성적": ("성적", "평점", "평균평점"),
    "교직": ("교직", "교직과정"),
    "현장실습": ("현장실습", "현장실습학기제"),
}

STRICT_QUERY_TERMS = {
    "복수전공",
    "부전공",
    "전과",
    "학점",
    "수강신청",
    "휴학",
    "복학",
    "장학",
    "등록금",
    "성적",
    "교직",
    "현장실습",
}

DATASET_PRIORITIES = {
    "match_rule_documents": 1.00,
    "match_pknu_notice_documents": 0.70,
    "match_pknu_student_life_documents": 0.70,
    "match_rag_documents": 0.40,
}

# Query-time dataset routing. 특정 키워드가 질문에 있으면 관련 dataset의
# priority에 boost를 얹어서 상위로 끌어올림. 여러 규칙이 매칭되면 합산.
# 시연 필수 3개(Q1 졸업학점, Q2 졸업유예, Q3 캡스톤)에 회귀 없이 Q4(복수전공)
# 개선을 노림.
DATASET_BOOST_RULES: tuple[tuple[tuple[str, ...], Dict[str, float]], ...] = (
    (
        (
            "휴학", "복학", "결석", "출석", "재수강", "재이수", "전과",
            "졸업요건", "복수전공", "부전공", "학사경고", "학점",
            "학사학위취득유예", "졸업유예",
        ),
        {"match_rule_documents": 0.15, "match_pknu_student_life_documents": 0.10},
    ),
    (
        (
            "셔틀", "생활관", "기숙사", "도서관", "식당",
            "보건진료소", "연락처", "사무실", "전화",
        ),
        {"match_pknu_student_life_documents": 0.15},
    ),
    (
        ("캡스톤", "학부 사무실", "학과 사무실"),
        {"match_rag_documents": 0.10},
    ),
)

SOURCE_KIND_PRIORITIES = {
    "post": 1.00,
    "page": 1.00,
    "html": 1.00,
    "attachment": 0.65,
    "archive_member": 0.55,
}


class Reranker(Protocol):
    def score(self, question: str, rows: List[Dict[str, Any]]) -> List[float]:
        ...


def build_supabase_client(url: str, key: str) -> Client:
    return create_client(url, key)


def _vector_literal(vec: List[float]) -> str:
    """pgvector accepts text literal '[v1,v2,...]'."""
    return "[" + ",".join(repr(float(v)) for v in vec) + "]"


def _similarity(row: Dict[str, Any]) -> float:
    try:
        return float(row.get("similarity") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _priority_score(row: Dict[str, Any]) -> float:
    metadata = row.get("metadata") or {}
    value = row.get("priority_score", metadata.get("priority_score"))
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _dataset_priority(row: Dict[str, Any]) -> float:
    override = row.get("_boosted_dataset_priority")
    if override is not None:
        try:
            return float(override)
        except (TypeError, ValueError):
            pass
    metadata = row.get("metadata") or {}
    rpc_name = str(metadata.get("rpc_name") or "")
    return DATASET_PRIORITIES.get(rpc_name, 0.50)


def _dataset_priority_boost(rpc_name: str, query_text: Optional[str]) -> float:
    if not query_text:
        return 0.0
    text = _normalize_text(query_text)
    if not text:
        return 0.0
    boost = 0.0
    for keywords, boosts in DATASET_BOOST_RULES:
        if any(kw in text for kw in keywords):
            boost += boosts.get(rpc_name, 0.0)
    return boost


def _source_kind_priority(row: Dict[str, Any]) -> float:
    metadata = row.get("metadata") or {}
    source_kind = str(metadata.get("source_kind") or "").strip().lower()
    source_ext = str(metadata.get("source_ext") or "").strip().lower()
    if source_ext in {".html", ".htm", ".json"} and not source_kind:
        source_kind = "html"
    return SOURCE_KIND_PRIORITIES.get(source_kind, 0.70)


def _final_score(
    row: Dict[str, Any],
    priority_weight: float,
    dataset_priority_weight: float,
    source_kind_weight: float,
) -> float:
    bounded_weight = max(0.0, min(1.0, priority_weight))
    bounded_dataset_weight = max(0.0, min(1.0, dataset_priority_weight))
    bounded_source_kind_weight = max(0.0, min(1.0, source_kind_weight))
    semantic_weight = max(
        0.0,
        1.0 - bounded_weight - bounded_dataset_weight - bounded_source_kind_weight,
    )
    return (
        _similarity(row) * semantic_weight
        + _priority_score(row) * bounded_weight
        + _dataset_priority(row) * bounded_dataset_weight
        + _source_kind_priority(row) * bounded_source_kind_weight
    )


def _normalize_text(text: str) -> str:
    return " ".join(str(text or "").replace("\u00a0", " ").split()).strip()


def _title_for_row(row: Dict[str, Any]) -> str:
    metadata = row.get("metadata") or {}
    return _normalize_text(
        metadata.get("doc_title")
        or metadata.get("source_file")
        or metadata.get("title")
        or row.get("title")
        or row.get("source_slug")
        or ""
    )


def _noise_flags(row: Dict[str, Any]) -> list[str]:
    text = _normalize_text(row.get("content") or row.get("text") or "")
    title = _title_for_row(row)
    if not text:
        return ["empty"]

    tokens = re.findall(r"[0-9A-Za-z\uac00-\ud7a3]+", text.lower())
    unique_tokens = set(tokens)
    hangul_count = len(re.findall(r"[\uac00-\ud7a3]", text))
    digit_count = len(re.findall(r"\d", text))
    dot_count = text.count(".") + text.count("·") + text.count("_")
    compact_len = max(1, len(re.sub(r"\s+", "", text)))
    lower_text = text.lower()
    flags: list[str] = []
    keyword_hits = sum(1 for keyword in ACADEMIC_KEYWORDS if keyword in text)

    if len(text) < 100 and len(unique_tokens) <= 6 and keyword_hits < 3:
        flags.append("metadata_stub")
    if dot_count / compact_len >= 0.20:
        flags.append("punctuation_heavy")
    if digit_count / compact_len >= 0.60 and hangul_count < 20:
        flags.append("numeric_heavy")
    if re.fullmatch(r"[\d\s.,:;~\-–—·ㆍ/()]+", text):
        flags.append("no_words")
    if any(phrase.lower() in lower_text for phrase in NOISE_PHRASES) and len(text) < 300:
        flags.append("template_or_form_noise")

    title_compact = re.sub(r"\s+", "", title)
    text_compact = re.sub(r"\s+", "", text)
    generic_titles = {re.sub(r"\s+", "", item) for item in GENERIC_STUB_TITLES}
    if title_compact in generic_titles and len(text) < 200:
        flags.append("generic_title_stub")
    if title_compact and text_compact and title_compact in text_compact:
        remainder = text_compact.replace(title_compact, "")
        if len(text) < 180 and len(remainder) <= 20:
            flags.append("title_only_stub")

    # Keep short chunks only when they contain enough concrete academic signal.
    if len(text) < 80 and keyword_hits < 2:
        flags.append("weak_short_chunk")

    # 반복 문자/자모 스팸 ("ㅇㅇㅇㅇㅇ", "!!!!!", "~~~~~")
    if _REPEATED_CHAR_RE.search(text):
        flags.append("repeated_char_spam")
    if _STANDALONE_JAMO_RE.search(text):
        flags.append("standalone_jamo_spam")

    return flags


def _is_noisy_row(row: Dict[str, Any]) -> bool:
    return bool(_noise_flags(row))


def _query_term_groups(query_text: Optional[str]) -> list[tuple[str, tuple[str, ...]]]:
    normalized = _normalize_text(query_text or "")
    if not normalized:
        return []
    return [
        (term, variants)
        for term, variants in QUERY_TERM_GROUPS.items()
        if any(variant in normalized for variant in variants)
    ]


def _query_mismatch_flags(
    row: Dict[str, Any],
    query_terms: list[tuple[str, tuple[str, ...]]],
) -> list[str]:
    if not query_terms:
        return []

    haystack = _normalize_text(f"{_title_for_row(row)} {row.get('content') or ''}")
    missing_strict = []
    matched = 0
    matched_terms: set[str] = set()
    for term, variants in query_terms:
        if any(variant in haystack for variant in variants):
            matched += 1
            matched_terms.add(term)
        elif term in STRICT_QUERY_TERMS:
            missing_strict.append(term)

    flags: list[str] = []
    if missing_strict:
        flags.append("missing_strict_query_terms:" + ",".join(missing_strict))
    if len(query_terms) >= 2 and matched == 0:
        flags.append("no_query_term_overlap")
    term_names = {term for term, _ in query_terms}
    if {"졸업", "학점"}.issubset(term_names) and not {"졸업", "학점"}.issubset(
        matched_terms
    ):
        flags.append("missing_graduation_credit_terms")
    return flags


def _dataset_mismatch_flags(
    row: Dict[str, Any],
    query_terms: list[tuple[str, tuple[str, ...]]],
) -> list[str]:
    term_names = {term for term, _ in query_terms}
    if not term_names:
        return []

    metadata = row.get("metadata") or {}
    rpc_name = str(metadata.get("rpc_name") or "")
    haystack = _normalize_text(f"{_title_for_row(row)} {row.get('content') or ''}")
    flags: list[str] = []

    if {"졸업", "학점"}.issubset(term_names):
        if not any(
            keyword in haystack
            for keyword in ("졸업요건", "졸업 소요학점", "소요학점", "교육과정 편성")
        ):
            flags.append("not_suitable_for_graduation_credit")

    return flags


def _row_uri(row: Dict[str, Any]) -> str:
    metadata = row.get("metadata") or {}
    return (
        row.get("uri")
        or row.get("url")
        or metadata.get("doc_url")
        or metadata.get("source_page_url")
        or metadata.get("attachment_url")
        or ""
    )


def _url_key(row: Dict[str, Any]) -> str:
    """Per-URL cap 용 key. 쿼리스트링/앵커 제거해서 같은 문서 다른 뷰 통합."""
    uri = re.sub(r"[?#].*$", "", _normalize_text(_row_uri(row)).lower())
    if uri:
        return f"url:{uri}"
    return f"title:{_normalize_text(_title_for_row(row)).lower()}"


def _dedupe_key(row: Dict[str, Any]) -> str:
    """정확히 같은 chunk 제거용 (같은 chunk가 여러 RPC에서 반환되는 경우 대비)."""
    uri = _row_uri(row)
    title = _title_for_row(row)
    metadata = row.get("metadata") or {}
    content = str(row.get("content") or "")
    if content:
        digest = hashlib.sha1(
            _normalize_text(content)[:500].encode("utf-8", errors="ignore")
        ).hexdigest()
        return f"{uri}|{title}|{digest}"

    for key in ("id", "chunk_id", "document_id"):
        value = row.get(key) or metadata.get(key)
        if value:
            return f"{key}:{value}"
    return f"{uri}|{title}"


def _add_row(
    selected: List[Dict[str, Any]],
    seen: set[str],
    url_counts: Dict[str, int],
    row: Dict[str, Any],
    *,
    max_chunks_per_url: int,
) -> bool:
    key = _dedupe_key(row)
    if key in seen:
        return False
    url_key = _url_key(row)
    if url_counts.get(url_key, 0) >= max_chunks_per_url:
        return False
    seen.add(key)
    url_counts[url_key] = url_counts.get(url_key, 0) + 1
    selected.append(row)
    return True


def _cap_per_url(
    rows: List[Dict[str, Any]], *, max_chunks_per_url: int
) -> List[Dict[str, Any]]:
    """Preserve input order, keep at most `max_chunks_per_url` rows per URL."""
    counts: Dict[str, int] = {}
    kept: List[Dict[str, Any]] = []
    for row in rows:
        url_key = _url_key(row)
        if counts.get(url_key, 0) >= max_chunks_per_url:
            continue
        counts[url_key] = counts.get(url_key, 0) + 1
        kept.append(row)
    return kept


def _rerank_candidates(
    *,
    question: str,
    candidates: List[Dict[str, Any]],
    top_k: int,
    max_chunks_per_url: int,
    reranker: Optional[Reranker],
    reranker_weight: float,
    priority_weight: float,
    dataset_priority_weight: float,
    source_kind_weight: float,
) -> List[Dict[str, Any]]:
    if not candidates:
        return []

    if not reranker or not question.strip():
        ordered = sorted(
            candidates,
            key=lambda row: _final_score(
                row,
                priority_weight,
                dataset_priority_weight,
                source_kind_weight,
            ),
            reverse=True,
        )
        return _cap_per_url(ordered, max_chunks_per_url=max_chunks_per_url)[:top_k]

    try:
        raw_scores = reranker.score(question, candidates)
    except Exception:
        logger.exception("retrieval: reranker failed, falling back to initial scores")
        ordered = sorted(
            candidates,
            key=lambda row: _final_score(
                row,
                priority_weight,
                dataset_priority_weight,
                source_kind_weight,
            ),
            reverse=True,
        )
        return _cap_per_url(ordered, max_chunks_per_url=max_chunks_per_url)[:top_k]

    normalized_scores = normalize_scores(raw_scores)
    bounded_reranker_weight = max(0.0, min(1.0, reranker_weight))
    initial_weight = 1.0 - bounded_reranker_weight
    rescored: List[Dict[str, Any]] = []
    for row, rerank_score in zip(candidates, normalized_scores):
        copied = dict(row)
        initial_score = _final_score(
            copied,
            priority_weight,
            dataset_priority_weight,
            source_kind_weight,
        )
        rerank_final_score = (
            rerank_score * bounded_reranker_weight
            + initial_score * initial_weight
        )
        copied["rerank_score"] = rerank_score
        copied["rerank_final_score"] = rerank_final_score
        rescored.append(copied)

    rescored.sort(key=lambda row: float(row.get("rerank_final_score") or 0.0), reverse=True)
    return _cap_per_url(rescored, max_chunks_per_url=max_chunks_per_url)[:top_k]


def search(
    client: Client,
    *,
    rpc_names: List[str],
    embedding: List[float],
    top_k: int,
    first_stage_k: int,
    min_similarity: float,
    priority_weight: float = 0.30,
    dataset_priority_weight: float = 0.15,
    source_kind_weight: float = 0.10,
    reranker: Optional[Reranker] = None,
    reranker_weight: float = 0.80,
    max_chunks_per_url: int = 2,
    query_text: Optional[str] = None,
    metadata_filter: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Fan out across RPCs, merge rows, then return a diverse top_k set.

    Each RPC must accept the same (query_embedding, match_count, min_similarity,
    metadata_filter) signature and return rows with a `similarity` field. RPCs
    apply min_similarity in DB, so the merged set is already filtered. A failure
    in one RPC is logged at WARN and skipped; partial results still flow.
    """
    query_terms = _query_term_groups(query_text)
    candidate_count = max(first_stage_k, top_k)
    payload = {
        "query_embedding": _vector_literal(embedding),
        "match_count": candidate_count,
        "min_similarity": min_similarity,
        "metadata_filter": metadata_filter or {},
    }

    per_rpc_rows: List[List[Dict[str, Any]]] = []
    merged: List[Dict[str, Any]] = []
    for rpc_name in rpc_names:
        try:
            started = time.perf_counter()
            response = client.rpc(rpc_name, payload).execute()
            elapsed_ms = (time.perf_counter() - started) * 1000
        except Exception:
            logger.warning("retrieval: rpc=%s failed, skipping", rpc_name, exc_info=True)
            continue

        rows: List[Dict[str, Any]] = []
        filtered_noise = 0
        filtered_query_mismatch = 0
        filtered_dataset_mismatch = 0
        for row in response.data or []:
            copied = dict(row)
            metadata = dict(copied.get("metadata") or {})
            metadata.setdefault("rpc_name", rpc_name)
            copied["metadata"] = metadata
            noise_flags = _noise_flags(copied)
            if noise_flags:
                filtered_noise += 1
                if logger.isEnabledFor(logging.DEBUG):
                    logger.debug(
                        "retrieval: filtered_noise rpc=%s flags=%s title=%r sim=%.4f",
                        rpc_name,
                        noise_flags,
                        _title_for_row(copied)[:120],
                        _similarity(copied),
                    )
                continue
            query_flags = _query_mismatch_flags(copied, query_terms)
            if query_flags:
                filtered_query_mismatch += 1
                if logger.isEnabledFor(logging.DEBUG):
                    logger.debug(
                        "retrieval: filtered_query_mismatch rpc=%s flags=%s title=%r sim=%.4f",
                        rpc_name,
                        query_flags,
                        _title_for_row(copied)[:120],
                        _similarity(copied),
                    )
                continue
            dataset_flags = _dataset_mismatch_flags(copied, query_terms)
            if dataset_flags:
                filtered_dataset_mismatch += 1
                if logger.isEnabledFor(logging.DEBUG):
                    logger.debug(
                        "retrieval: filtered_dataset_mismatch rpc=%s flags=%s title=%r sim=%.4f",
                        rpc_name,
                        dataset_flags,
                        _title_for_row(copied)[:120],
                        _similarity(copied),
                    )
                continue
            base_dataset_priority = DATASET_PRIORITIES.get(rpc_name, 0.50)
            boost = _dataset_priority_boost(rpc_name, query_text)
            copied["_boosted_dataset_priority"] = min(
                1.0, base_dataset_priority + boost
            )
            copied["priority_score"] = _priority_score(copied)
            copied["dataset_priority"] = _dataset_priority(copied)
            copied["final_score"] = _final_score(
                copied,
                priority_weight,
                dataset_priority_weight,
                source_kind_weight,
            )
            rows.append(copied)

        rows.sort(
            key=lambda row: _final_score(
                row,
                priority_weight,
                dataset_priority_weight,
                source_kind_weight,
            ),
            reverse=True,
        )
        logger.info(
            "retrieval: rpc=%s rows=%d filtered_noise=%d filtered_query_mismatch=%d filtered_dataset_mismatch=%d latency_ms=%.1f top_similarity=%.4f top_priority=%.4f top_dataset_priority=%.2f top_source_kind_priority=%.2f top_final=%.4f priority_weight=%.2f dataset_priority_weight=%.2f source_kind_weight=%.2f",
            rpc_name,
            len(rows),
            filtered_noise,
            filtered_query_mismatch,
            filtered_dataset_mismatch,
            elapsed_ms,
            _similarity(rows[0]) if rows else 0.0,
            _priority_score(rows[0]) if rows else 0.0,
            _dataset_priority(rows[0]) if rows else 0.0,
            _source_kind_priority(rows[0]) if rows else 0.0,
            _final_score(
                rows[0],
                priority_weight,
                dataset_priority_weight,
                source_kind_weight,
            )
            if rows
            else 0.0,
            priority_weight,
            dataset_priority_weight,
            source_kind_weight,
        )
        per_rpc_rows.append(rows)
        merged.extend(rows)

    candidates: List[Dict[str, Any]] = []
    seen: set[str] = set()
    url_counts: Dict[str, int] = {}

    # Keep one high-scoring row from each successful RPC before global ranking.
    # This prevents one noisy collection from crowding out regulations/notices.
    for rows in per_rpc_rows:
        if len(candidates) >= candidate_count:
            break
        for row in rows:
            if _add_row(
                candidates,
                seen,
                url_counts,
                row,
                max_chunks_per_url=max_chunks_per_url,
            ):
                break

    merged.sort(
        key=lambda row: _final_score(
            row,
            priority_weight,
            dataset_priority_weight,
            source_kind_weight,
        ),
        reverse=True,
    )
    for row in merged:
        if len(candidates) >= candidate_count:
            break
        _add_row(
            candidates,
            seen,
            url_counts,
            row,
            max_chunks_per_url=max_chunks_per_url,
        )

    selected = _rerank_candidates(
        question=query_text or "",
        candidates=candidates,
        top_k=top_k,
        max_chunks_per_url=max_chunks_per_url,
        reranker=reranker,
        reranker_weight=reranker_weight,
        priority_weight=priority_weight,
        dataset_priority_weight=dataset_priority_weight,
        source_kind_weight=source_kind_weight,
    )

    logger.info(
        "retrieval: merged=%d candidates=%d trimmed=%d top_sim=%.3f top_priority=%.3f top_dataset_priority=%.2f top_source_kind_priority=%.2f top_final=%.3f top_rerank=%.3f priority_weight=%.2f dataset_priority_weight=%.2f source_kind_weight=%.2f reranker=%s",
        len(merged),
        len(candidates),
        len(selected),
        _similarity(selected[0]) if selected else 0.0,
        _priority_score(selected[0]) if selected else 0.0,
        _dataset_priority(selected[0]) if selected else 0.0,
        _source_kind_priority(selected[0]) if selected else 0.0,
        _final_score(
            selected[0],
            priority_weight,
            dataset_priority_weight,
            source_kind_weight,
        )
        if selected
        else 0.0,
        float(selected[0].get("rerank_score") or 0.0) if selected else 0.0,
        priority_weight,
        dataset_priority_weight,
        source_kind_weight,
        bool(reranker),
    )
    if logger.isEnabledFor(logging.DEBUG):
        selected_summaries = []
        for index, row in enumerate(selected, start=1):
            metadata = row.get("metadata") or {}
            title = (
                metadata.get("doc_title")
                or metadata.get("source_file")
                or metadata.get("title")
                or row.get("title")
                or row.get("source_slug")
                or ""
            )
            selected_summaries.append(
                {
                    "index": index,
                    "rpc": metadata.get("rpc_name"),
                    "title": str(title)[:120],
                    "similarity": round(_similarity(row), 4),
                    "priority_score": round(_priority_score(row), 4),
                    "dataset_priority": round(_dataset_priority(row), 4),
                    "source_kind_priority": round(_source_kind_priority(row), 4),
                    "final_score": round(
                        _final_score(
                            row,
                            priority_weight,
                            dataset_priority_weight,
                            source_kind_weight,
                        ),
                        4,
                    ),
                    "rerank_score": round(float(row.get("rerank_score") or 0.0), 4),
                    "rerank_final_score": round(
                        float(row.get("rerank_final_score") or 0.0), 4
                    ),
                }
            )
        logger.debug("retrieval: selected=%s", selected_summaries)
    return selected

"""Query Supabase PostgreSQL RAG chunks with the same embedding model."""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

try:
    import psycopg
    from psycopg import sql
    from psycopg.rows import dict_row
except ModuleNotFoundError:
    psycopg = None
    sql = None
    dict_row = None

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
EXPECTED_DIMENSIONS = 384
DATASET_MATCH_FUNCTIONS = {
    "ce": "match_rag_documents",
    "pknu_notice": "match_pknu_notice_documents",
    "pknu_student_life": "match_pknu_student_life_documents",
    "rule": "match_rule_documents",
}
DATASET_TABLES = {
    "ce": {
        "sources": "rag_sources",
        "chunks": "rag_chunks",
    },
    "pknu_notice": {
        "sources": "pknu_notice_sources",
        "chunks": "pknu_notice_chunks",
    },
    "pknu_student_life": {
        "sources": "pknu_student_life_sources",
        "chunks": "pknu_student_life_chunks",
    },
    "rule": {
        "sources": "rule_sources",
        "chunks": "rule_chunks",
    },
}


def connect() -> Any:
    if psycopg is None or dict_row is None:
        raise RuntimeError("psycopg is required for Supabase queries. Install backend requirements first.")

    load_dotenv(PROJECT_ROOT / "backend" / ".env")
    conninfo = os.getenv("DATABASE_URL") or os.getenv("SUPABASE_DB_URL")
    if conninfo:
        return psycopg.connect(conninfo, row_factory=dict_row, prepare_threshold=None)

    required = ["PGHOST", "PGDATABASE", "PGUSER", "PGPASSWORD"]
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        raise RuntimeError(
            "Missing database configuration. Set DATABASE_URL or SUPABASE_DB_URL, "
            f"or set {', '.join(required)}."
        )

    return psycopg.connect(
        host=os.environ["PGHOST"],
        port=os.getenv("PGPORT", "5432"),
        dbname=os.environ["PGDATABASE"],
        user=os.environ["PGUSER"],
        password=os.environ["PGPASSWORD"],
        sslmode=os.getenv("PGSSLMODE", "require"),
        row_factory=dict_row,
        prepare_threshold=None,
    )


def embed_query(question: str, model_name: str) -> list[float]:
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(model_name)
    if hasattr(model, "get_embedding_dimension"):
        get_dimensions = model.get_embedding_dimension
    else:
        get_dimensions = model.get_sentence_embedding_dimension
    dimensions = int(get_dimensions() or 0)
    if dimensions != EXPECTED_DIMENSIONS:
        raise ValueError(
            f"Model {model_name} produces {dimensions} dimensions, expected {EXPECTED_DIMENSIONS}."
        )

    vector = model.encode([question], normalize_embeddings=True, show_progress_bar=False)[0]
    return [round(float(value), 8) for value in vector]


def vector_literal(values: list[float]) -> str:
    return "[" + ",".join(str(float(value)) for value in values) + "]"


def validate_function_name(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise ValueError(f"Invalid function name: {value}")
    return value


def validate_table_name(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise ValueError(f"Invalid table name: {value}")
    return value


def search(
    question: str,
    model_name: str,
    top_k: int,
    min_similarity: float,
    dataset: str,
    match_function: str | None = None,
) -> list[dict[str, Any]]:
    if sql is None:
        raise RuntimeError("psycopg is required for Supabase queries. Install backend requirements first.")

    embedding = vector_literal(embed_query(question, model_name))
    function_name = validate_function_name(match_function or DATASET_MATCH_FUNCTIONS[dataset])
    query = sql.SQL("""
        select *
        from {match_function}(
            %(embedding)s::extensions.vector(384),
            %(top_k)s,
            %(min_similarity)s,
            '{{}}'::jsonb
        )
    """).format(match_function=sql.Identifier("public", function_name))
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                query,
                {
                    "embedding": embedding,
                    "top_k": top_k,
                    "min_similarity": min_similarity,
                },
            )
            return list(cur.fetchall())


def search_with_priority(
    question: str,
    model_name: str,
    top_k: int,
    min_similarity: float,
    dataset: str,
    rank_by: str,
    priority_weight: float,
) -> list[dict[str, Any]]:
    if sql is None:
        raise RuntimeError("psycopg is required for Supabase queries. Install backend requirements first.")

    tables = DATASET_TABLES[dataset]
    sources_table = validate_table_name(tables["sources"])
    chunks_table = validate_table_name(tables["chunks"])

    if rank_by == "priority":
        query = sql.SQL("""
            select
                null::text as id,
                s.id as source_id,
                s.source_slug,
                s.source_type,
                s.title,
                s.url,
                s.parent_url,
                null::integer as chunk_id,
                null::integer as chunk_index,
                coalesce(left(string_agg(c.content, E'\n\n' order by c.chunk_index), 1200), '') as content,
                s.metadata,
                null::double precision as similarity,
                s.priority_score,
                s.priority_details,
                s.priority_score as final_score
            from public.{sources_table} as s
            left join public.{chunks_table} as c on c.source_id = s.id
            where s.status = 'active'
            group by
                s.id, s.source_slug, s.source_type, s.title, s.url, s.parent_url,
                s.metadata, s.priority_score, s.priority_details
            order by s.priority_score desc, s.updated_at desc
            limit %(top_k)s
        """).format(
            sources_table=sql.Identifier(sources_table),
            chunks_table=sql.Identifier(chunks_table),
        )
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(query, {"top_k": top_k})
                return list(cur.fetchall())

    embedding = vector_literal(embed_query(question, model_name))
    order_expression = sql.SQL("similarity desc")
    if rank_by == "hybrid":
        order_expression = sql.SQL("final_score desc, similarity desc")

    query = sql.SQL("""
        with scored as (
            select
                c.id,
                c.source_id,
                s.source_slug,
                s.source_type,
                s.title,
                s.url,
                s.parent_url,
                c.chunk_id,
                c.chunk_index,
                c.content,
                s.metadata || c.metadata as metadata,
                1.0 - (c.embedding <=> %(embedding)s::extensions.vector(384)) as similarity,
                s.priority_score,
                s.priority_details
            from public.{chunks_table} as c
            join public.{sources_table} as s on s.id = c.source_id
            where s.status = 'active'
              and 1.0 - (c.embedding <=> %(embedding)s::extensions.vector(384)) >= %(min_similarity)s
        )
        select
            *,
            (similarity * (1.0 - %(priority_weight)s))
                + (priority_score * %(priority_weight)s) as final_score
        from scored
        order by {order_expression}
        limit %(top_k)s
    """).format(
        sources_table=sql.Identifier(sources_table),
        chunks_table=sql.Identifier(chunks_table),
        order_expression=order_expression,
    )
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                query,
                {
                    "embedding": embedding,
                    "top_k": top_k,
                    "min_similarity": min_similarity,
                    "priority_weight": priority_weight,
                },
            )
            return list(cur.fetchall())


def print_results(rows: list[dict[str, Any]]) -> None:
    for rank, row in enumerate(rows, start=1):
        metadata = row.get("metadata") or {}
        title = row.get("title") or metadata.get("doc_title") or metadata.get("source_file") or row["source_slug"]
        url = (
            row.get("url")
            or row.get("parent_url")
            or metadata.get("doc_url")
            or metadata.get("source_page_url")
            or metadata.get("attachment_url")
            or ""
        )
        content = " ".join(str(row["content"]).split())
        snippet = content[:300] + ("..." if len(content) > 300 else "")

        scores = []
        if row.get("similarity") is not None:
            scores.append(f"similarity={row['similarity']:.4f}")
        if row.get("priority_score") is not None:
            scores.append(f"priority={row['priority_score']:.4f}")
        if row.get("final_score") is not None:
            scores.append(f"final={row['final_score']:.4f}")
        print(f"\n#{rank} {' '.join(scores)}")
        print(f"title: {title}")
        if url:
            print(f"url: {url}")
        print(f"source_slug: {row['source_slug']} chunk_index: {row.get('chunk_index')}")
        if row.get("priority_details"):
            print(f"priority_details: {row['priority_details']}")
        print(f"snippet: {snippet}")


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="Semantic search against Supabase RAG chunks.")
    parser.add_argument("question", nargs="?")
    parser.add_argument("--dataset", choices=sorted(DATASET_MATCH_FUNCTIONS), default="ce")
    parser.add_argument("--match-function", help="Override public schema match function name.")
    parser.add_argument("--model-name", default=DEFAULT_MODEL)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--min-similarity", type=float, default=0.0)
    parser.add_argument(
        "--rank-by",
        choices=["similarity", "priority", "hybrid"],
        default="similarity",
        help=(
            "similarity: 기존 RPC 검색, priority: source priority 순 조회, "
            "hybrid: similarity와 priority_score를 섞어 정렬"
        ),
    )
    parser.add_argument(
        "--include-priority",
        action="store_true",
        help="similarity 정렬에서도 priority_score를 함께 출력하기 위해 직접 SQL 검색을 사용한다.",
    )
    parser.add_argument(
        "--priority-weight",
        type=float,
        default=0.15,
        help="--rank-by hybrid에서 priority_score 반영 비율. 기본값: 0.15",
    )
    args = parser.parse_args()

    if args.top_k <= 0:
        raise ValueError("--top-k must be positive")
    if not 0.0 <= args.priority_weight <= 1.0:
        raise ValueError("--priority-weight must be between 0.0 and 1.0")
    if args.rank_by != "priority" and not args.question:
        raise ValueError("question is required unless --rank-by priority is used")
    if args.match_function and (args.rank_by != "similarity" or args.include_priority):
        raise ValueError("--match-function can only be used with the default similarity RPC search")

    if args.rank_by != "similarity" or args.include_priority:
        rows = search_with_priority(
            args.question or "",
            args.model_name,
            args.top_k,
            args.min_similarity,
            args.dataset,
            args.rank_by,
            args.priority_weight,
        )
    else:
        rows = search(
            args.question or "",
            args.model_name,
            args.top_k,
            args.min_similarity,
            args.dataset,
            args.match_function,
        )
    print_results(rows)


if __name__ == "__main__":
    main()

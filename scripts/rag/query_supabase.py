"""Query Supabase PostgreSQL RAG chunks with the same embedding model."""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from typing import Any

import psycopg
from dotenv import load_dotenv
from psycopg import sql
from psycopg.rows import dict_row

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
EXPECTED_DIMENSIONS = 384
DATASET_MATCH_FUNCTIONS = {
    "ce": "match_rag_documents",
    "rule": "match_rule_documents",
}


def connect() -> psycopg.Connection:
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


def search(
    question: str,
    model_name: str,
    top_k: int,
    min_similarity: float,
    dataset: str,
    match_function: str | None = None,
) -> list[dict[str, Any]]:
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

        print(f"\n#{rank} similarity={row['similarity']:.4f}")
        print(f"title: {title}")
        if url:
            print(f"url: {url}")
        print(f"source_slug: {row['source_slug']} chunk_index: {row['chunk_index']}")
        print(f"snippet: {snippet}")


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="Semantic search against Supabase RAG chunks.")
    parser.add_argument("question")
    parser.add_argument("--dataset", choices=sorted(DATASET_MATCH_FUNCTIONS), default="ce")
    parser.add_argument("--match-function", help="Override public schema match function name.")
    parser.add_argument("--model-name", default=DEFAULT_MODEL)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--min-similarity", type=float, default=0.0)
    args = parser.parse_args()

    rows = search(
        args.question,
        args.model_name,
        args.top_k,
        args.min_similarity,
        args.dataset,
        args.match_function,
    )
    print_results(rows)


if __name__ == "__main__":
    main()

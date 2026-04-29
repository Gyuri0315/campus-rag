"""Load vectorized RAG chunks into Supabase PostgreSQL with pgvector.

Expected input:
    FILES/vectorized/index.jsonl

Required environment:
    DATABASE_URL or SUPABASE_DB_URL

Alternative PG environment variables are also supported:
    PGHOST, PGPORT, PGDATABASE, PGUSER, PGPASSWORD, PGSSLMODE
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Iterable

import psycopg
from dotenv import load_dotenv
from psycopg.types.json import Jsonb

DEFAULT_INDEX_PATH = Path("FILES/vectorized/index.jsonl")
EXPECTED_DIMENSIONS = 384


def vector_literal(values: list[float]) -> str:
    return "[" + ",".join(str(float(value)) for value in values) + "]"


def connect() -> psycopg.Connection:
    load_dotenv()
    conninfo = os.getenv("DATABASE_URL") or os.getenv("SUPABASE_DB_URL")
    if conninfo:
        return psycopg.connect(conninfo, prepare_threshold=None)

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
        prepare_threshold=None,
    )


def iter_records(index_path: Path) -> Iterable[dict[str, Any]]:
    with index_path.open("r", encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON on line {line_number}: {exc}") from exc


def prepare_row(record: dict[str, Any]) -> dict[str, Any]:
    embedding = record.get("embedding")
    if not isinstance(embedding, list):
        raise ValueError(f"Record {record.get('id')} has no embedding list")
    if len(embedding) != EXPECTED_DIMENSIONS:
        raise ValueError(
            f"Record {record.get('id')} has embedding dimension {len(embedding)}, "
            f"expected {EXPECTED_DIMENSIONS}. Re-run: python vectorization.py --backend sentence-transformers"
        )

    content = record.get("text") or record.get("content") or ""
    if not content:
        raise ValueError(f"Record {record.get('id')} has empty text/content")

    return {
        "id": str(record["id"]),
        "source_slug": str(record["source_slug"]),
        "chunk_id": int(record["chunk_id"]),
        "chunk_index": int(record["chunk_index"]),
        "content": str(content),
        "metadata": Jsonb(record.get("metadata") or {}),
        "embedding": vector_literal(embedding),
    }


def flush_batch(conn: psycopg.Connection, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return

    sql = """
        insert into public.rag_documents (
            id, source_slug, chunk_id, chunk_index, content, metadata, embedding
        )
        values (
            %(id)s,
            %(source_slug)s,
            %(chunk_id)s,
            %(chunk_index)s,
            %(content)s,
            %(metadata)s,
            %(embedding)s::extensions.vector(384)
        )
        on conflict (id) do update set
            source_slug = excluded.source_slug,
            chunk_id = excluded.chunk_id,
            chunk_index = excluded.chunk_index,
            content = excluded.content,
            metadata = excluded.metadata,
            embedding = excluded.embedding
    """
    with conn.cursor() as cur:
        cur.executemany(sql, rows)


def load(index_path: Path, batch_size: int) -> int:
    if not index_path.exists():
        raise FileNotFoundError(f"Index file not found: {index_path}")

    total = 0
    batch: list[dict[str, Any]] = []
    with connect() as conn:
        for record in iter_records(index_path):
            batch.append(prepare_row(record))
            if len(batch) >= batch_size:
                flush_batch(conn, batch)
                total += len(batch)
                conn.commit()
                print(f"upserted {total} rows")
                batch.clear()

        flush_batch(conn, batch)
        total += len(batch)
        conn.commit()

    return total


def main() -> None:
    parser = argparse.ArgumentParser(description="Load RAG chunks into Supabase PostgreSQL.")
    parser.add_argument("--index", type=Path, default=DEFAULT_INDEX_PATH)
    parser.add_argument("--batch-size", type=int, default=200)
    args = parser.parse_args()

    count = load(args.index, args.batch_size)
    print(f"done: upserted {count} rows")


if __name__ == "__main__":
    main()

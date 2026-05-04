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
import hashlib
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import psycopg
from dotenv import load_dotenv
from psycopg.types.json import Jsonb

DEFAULT_INDEX_PATH = Path("FILES/vectorized/index.jsonl")
EXPECTED_DIMENSIONS = 384
DEFAULT_EMBEDDING_MODEL = "sentence-transformers:sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
PROJECT_ROOT = Path(__file__).resolve().parent
LOG_DIR = PROJECT_ROOT / "logs"
LOG_FILE = LOG_DIR / "load_to_supabase.log"

log = logging.getLogger(__name__)


def configure_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(LOG_FILE, encoding="utf-8"),
        ],
    )


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


def ensure_schema_ready(conn: psycopg.Connection) -> None:
    sql = """
        select
            to_regclass('public.rag_sources') is not null as has_sources,
            to_regclass('public.rag_chunks') is not null as has_chunks
    """
    with conn.cursor() as cur:
        cur.execute(sql)
        has_sources, has_chunks = cur.fetchone()

    missing = []
    if not has_sources:
        missing.append("public.rag_sources")
    if not has_chunks:
        missing.append("public.rag_chunks")
    if missing:
        raise RuntimeError(
            "Supabase schema is not ready. Missing tables: "
            f"{', '.join(missing)}. Apply supabase/migrations/002_split_rag_documents.sql "
            "before running load_to_supabase.py."
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


def load_embedding_model(index_path: Path) -> str:
    manifest_path = index_path.with_name("manifest.json")
    if not manifest_path.exists():
        return DEFAULT_EMBEDDING_MODEL

    with manifest_path.open("r", encoding="utf-8-sig") as handle:
        manifest = json.load(handle)
    return str(manifest.get("embedding_backend") or DEFAULT_EMBEDDING_MODEL)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def infer_source_type(metadata: dict[str, Any]) -> str:
    source_ext = str(metadata.get("source_ext") or "").lower().lstrip(".")
    if source_ext:
        return source_ext
    if metadata.get("attachment_url"):
        return "attachment"
    if metadata.get("doc_url") or metadata.get("source_page_url"):
        return "web"
    return "unknown"


def prepare_row(record: dict[str, Any], embedding_model: str) -> dict[str, dict[str, Any]]:
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

    metadata = record.get("metadata") or {}
    if not isinstance(metadata, dict):
        raise ValueError(f"Record {record.get('id')} has non-object metadata")

    source_slug = str(record["source_slug"])
    now = datetime.now(timezone.utc)

    return {
        "source": {
            "id": source_slug,
            "source_slug": source_slug,
            "source_type": infer_source_type(metadata),
            "title": metadata.get("doc_title") or metadata.get("source_file") or None,
            "url": metadata.get("doc_url") or metadata.get("attachment_url") or None,
            "parent_url": metadata.get("source_page_url") or None,
            "file_path": metadata.get("source_path") or None,
            "file_ext": metadata.get("source_ext") or None,
            "category": metadata.get("category") or None,
            "subcategory": metadata.get("subcategory") or None,
            "metadata": Jsonb(metadata),
            "processed_at": now,
        },
        "chunk": {
            "id": str(record["id"]),
            "source_id": source_slug,
            "chunk_id": int(record["chunk_id"]),
            "chunk_index": int(record["chunk_index"]),
            "content": str(content),
            "content_hash": sha256_text(str(content)),
            "token_count": len(str(content).split()),
            "metadata": Jsonb(metadata),
            "embedding": vector_literal(embedding),
            "embedding_model": embedding_model,
            "embedding_dim": EXPECTED_DIMENSIONS,
            "embedded_at": now,
        },
    }


def flush_batch(conn: psycopg.Connection, rows: list[dict[str, dict[str, Any]]]) -> None:
    if not rows:
        return

    source_sql = """
        insert into public.rag_sources (
            id, source_slug, source_type, title, url, parent_url, file_path,
            file_ext, category, subcategory, metadata, processed_at
        )
        values (
            %(id)s, %(source_slug)s, %(source_type)s, %(title)s, %(url)s,
            %(parent_url)s, %(file_path)s, %(file_ext)s, %(category)s,
            %(subcategory)s, %(metadata)s, %(processed_at)s
        )
        on conflict (id) do update set
            source_slug = excluded.source_slug,
            source_type = excluded.source_type,
            title = excluded.title,
            url = excluded.url,
            parent_url = excluded.parent_url,
            file_path = excluded.file_path,
            file_ext = excluded.file_ext,
            category = excluded.category,
            subcategory = excluded.subcategory,
            metadata = excluded.metadata,
            processed_at = excluded.processed_at
    """
    chunk_sql = """
        insert into public.rag_chunks (
            id, source_id, chunk_id, chunk_index, content, content_hash,
            token_count, metadata, embedding, embedding_model, embedding_dim, embedded_at
        )
        values (
            %(id)s,
            %(source_id)s,
            %(chunk_id)s,
            %(chunk_index)s,
            %(content)s,
            %(content_hash)s,
            %(token_count)s,
            %(metadata)s,
            %(embedding)s::extensions.vector(384),
            %(embedding_model)s,
            %(embedding_dim)s,
            %(embedded_at)s
        )
        on conflict (id) do update set
            source_id = excluded.source_id,
            chunk_id = excluded.chunk_id,
            chunk_index = excluded.chunk_index,
            content = excluded.content,
            content_hash = excluded.content_hash,
            token_count = excluded.token_count,
            metadata = excluded.metadata,
            embedding = excluded.embedding,
            embedding_model = excluded.embedding_model,
            embedding_dim = excluded.embedding_dim,
            embedded_at = excluded.embedded_at
    """
    sources = {row["source"]["id"]: row["source"] for row in rows}
    chunks = [row["chunk"] for row in rows]
    with conn.cursor() as cur:
        cur.executemany(source_sql, list(sources.values()))
        cur.executemany(chunk_sql, chunks)


def load(index_path: Path, batch_size: int) -> int:
    if not index_path.exists():
        raise FileNotFoundError(f"Index file not found: {index_path}")

    embedding_model = load_embedding_model(index_path)
    log.info("Loading Supabase rows from %s", index_path)
    log.info("embedding model: %s", embedding_model)
    total = 0
    batch: list[dict[str, dict[str, Any]]] = []
    with connect() as conn:
        ensure_schema_ready(conn)
        for record in iter_records(index_path):
            batch.append(prepare_row(record, embedding_model))
            if len(batch) >= batch_size:
                flush_batch(conn, batch)
                total += len(batch)
                conn.commit()
                log.info("upserted %d rows", total)
                batch.clear()

        flush_batch(conn, batch)
        total += len(batch)
        conn.commit()

    return total


def main() -> None:
    configure_logging()
    parser = argparse.ArgumentParser(description="Load RAG chunks into Supabase PostgreSQL.")
    parser.add_argument("--index", type=Path, default=DEFAULT_INDEX_PATH)
    parser.add_argument("--batch-size", type=int, default=200)
    args = parser.parse_args()

    count = load(args.index, args.batch_size)
    log.info("done: upserted %d rows", count)


if __name__ == "__main__":
    main()

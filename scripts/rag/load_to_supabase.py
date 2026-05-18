"""Load vectorized RAG chunks into Supabase PostgreSQL with pgvector.

Expected input:
    files/ce/vectorized/index.jsonl

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
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import psycopg
from dotenv import load_dotenv
from psycopg import sql
from psycopg.types.json import Jsonb

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATASET_TABLES = {
    "ce": {
        "index": PROJECT_ROOT / "files" / "ce" / "vectorized" / "index.jsonl",
        "sources": "rag_sources",
        "chunks": "rag_chunks",
    },
    "rule": {
        "index": PROJECT_ROOT / "files" / "rule" / "vectorized" / "index.jsonl",
        "sources": "rule_sources",
        "chunks": "rule_chunks",
    },
}
DEFAULT_DATASET = "ce"
EXPECTED_DIMENSIONS = 384
DEFAULT_EMBEDDING_MODEL = "sentence-transformers:sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
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


def with_connect_timeout(conninfo: str, timeout_seconds: int = 10) -> str:
    parts = urlsplit(conninfo)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query.setdefault("connect_timeout", str(timeout_seconds))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def connect() -> psycopg.Connection:
    load_dotenv(PROJECT_ROOT / "backend" / ".env")
    conninfo = os.getenv("DATABASE_URL") or os.getenv("SUPABASE_DB_URL")
    if conninfo:
        log.info("Connecting to Supabase PostgreSQL from DATABASE_URL/SUPABASE_DB_URL")
        try:
            return psycopg.connect(with_connect_timeout(conninfo), prepare_threshold=None)
        except psycopg.OperationalError as exc:
            if "Permission denied" in str(exc) and ".supabase.co" in conninfo:
                raise RuntimeError(
                    "Could not connect to Supabase PostgreSQL. If this is a direct "
                    "db.<project-ref>.supabase.co URL, use the Supabase connection "
                    "pooler URL instead; direct connections may require IPv6 access."
                ) from exc
            raise

    required = ["PGHOST", "PGDATABASE", "PGUSER", "PGPASSWORD"]
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        raise RuntimeError(
            "Missing database configuration. Set DATABASE_URL or SUPABASE_DB_URL, "
            f"or set {', '.join(required)}."
        )

    log.info("Connecting to Supabase PostgreSQL from PG* environment variables")
    return psycopg.connect(
        host=os.environ["PGHOST"],
        port=os.getenv("PGPORT", "5432"),
        dbname=os.environ["PGDATABASE"],
        user=os.environ["PGUSER"],
        password=os.environ["PGPASSWORD"],
        sslmode=os.getenv("PGSSLMODE", "require"),
        connect_timeout=10,
        prepare_threshold=None,
    )


def validate_table_name(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise ValueError(f"Invalid table name: {value}")
    return value


def resolve_tables(
    dataset: str,
    sources_table: str | None,
    chunks_table: str | None,
) -> tuple[str, str]:
    defaults = DATASET_TABLES[dataset]
    return (
        validate_table_name(sources_table or defaults["sources"]),
        validate_table_name(chunks_table or defaults["chunks"]),
    )


def resolve_index_path(dataset: str, index_path: Path | None) -> Path:
    if index_path is not None:
        return index_path
    return DATASET_TABLES[dataset]["index"]


def ensure_schema_ready(conn: psycopg.Connection, sources_table: str, chunks_table: str) -> None:
    log.info("Checking Supabase schema")
    sql = """
        select
            to_regclass(%s) is not null as has_sources,
            to_regclass(%s) is not null as has_chunks
    """
    with conn.cursor() as cur:
        cur.execute(sql, (f"public.{sources_table}", f"public.{chunks_table}"))
        has_sources, has_chunks = cur.fetchone()

    missing = []
    if not has_sources:
        missing.append(f"public.{sources_table}")
    if not has_chunks:
        missing.append(f"public.{chunks_table}")
    if missing:
        raise RuntimeError(
            "Supabase schema is not ready. Missing tables: "
            f"{', '.join(missing)}. Apply the matching migration under supabase/migrations "
            "before running scripts/rag/load_to_supabase.py."
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


def remove_nul_bytes(value: Any) -> Any:
    if isinstance(value, str):
        return value.replace("\x00", "")
    if isinstance(value, list):
        return [remove_nul_bytes(item) for item in value]
    if isinstance(value, dict):
        return {
            remove_nul_bytes(key): remove_nul_bytes(item)
            for key, item in value.items()
        }
    return value


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
            f"expected {EXPECTED_DIMENSIONS}. Re-run: python scripts/rag/vectorization.py --backend sentence-transformers"
        )

    content = remove_nul_bytes(record.get("text") or record.get("content") or "")
    if not content:
        raise ValueError(f"Record {record.get('id')} has empty text/content")

    metadata = remove_nul_bytes(record.get("metadata") or {})
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


def flush_batch(
    conn: psycopg.Connection,
    rows: list[dict[str, dict[str, Any]]],
    sources_table: str,
    chunks_table: str,
) -> None:
    if not rows:
        return

    source_sql = sql.SQL("""
        insert into {sources_table} (
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
    """).format(sources_table=sql.Identifier("public", sources_table))
    chunk_sql = sql.SQL("""
        insert into {chunks_table} (
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
        on conflict (source_id, chunk_index) do update set
            id = excluded.id,
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
    """).format(chunks_table=sql.Identifier("public", chunks_table))
    sources = {row["source"]["id"]: row["source"] for row in rows}
    chunks = [row["chunk"] for row in rows]
    with conn.cursor() as cur:
        cur.executemany(source_sql, list(sources.values()))
        cur.executemany(chunk_sql, chunks)


def load(
    index_path: Path,
    batch_size: int,
    dataset: str,
    sources_table: str | None = None,
    chunks_table: str | None = None,
) -> int:
    if not index_path.exists():
        raise FileNotFoundError(f"Index file not found: {index_path}")

    resolved_sources_table, resolved_chunks_table = resolve_tables(
        dataset,
        sources_table,
        chunks_table,
    )
    embedding_model = load_embedding_model(index_path)
    log.info("Loading Supabase rows from %s", index_path)
    log.info("target tables: public.%s, public.%s", resolved_sources_table, resolved_chunks_table)
    log.info("embedding model: %s", embedding_model)
    total = 0
    batch: list[dict[str, dict[str, Any]]] = []
    with connect() as conn:
        ensure_schema_ready(conn, resolved_sources_table, resolved_chunks_table)
        for record in iter_records(index_path):
            batch.append(prepare_row(record, embedding_model))
            if len(batch) >= batch_size:
                flush_batch(conn, batch, resolved_sources_table, resolved_chunks_table)
                total += len(batch)
                conn.commit()
                log.info("upserted %d rows", total)
                batch.clear()

        flush_batch(conn, batch, resolved_sources_table, resolved_chunks_table)
        total += len(batch)
        conn.commit()

    return total


def main() -> None:
    configure_logging()
    parser = argparse.ArgumentParser(description="Load RAG chunks into Supabase PostgreSQL.")
    parser.add_argument("--index", type=Path, default=None)
    parser.add_argument("--dataset", choices=sorted(DATASET_TABLES), default=DEFAULT_DATASET)
    parser.add_argument("--sources-table", help="Override source table name in public schema.")
    parser.add_argument("--chunks-table", help="Override chunk table name in public schema.")
    parser.add_argument("--batch-size", type=int, default=200)
    args = parser.parse_args()

    index_path = resolve_index_path(args.dataset, args.index)
    count = load(
        index_path,
        args.batch_size,
        args.dataset,
        args.sources_table,
        args.chunks_table,
    )
    log.info("done: upserted %d rows", count)


if __name__ == "__main__":
    main()

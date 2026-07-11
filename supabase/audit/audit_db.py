"""Read-only Supabase audit queries for pipeline check."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import psycopg
from dotenv import load_dotenv
from psycopg.rows import dict_row

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def connect() -> psycopg.Connection:
    load_dotenv(PROJECT_ROOT / "backend" / ".env")
    conninfo = os.getenv("DATABASE_URL") or os.getenv("SUPABASE_DB_URL")
    if conninfo:
        return psycopg.connect(conninfo, row_factory=dict_row, prepare_threshold=None)
    required = ["PGHOST", "PGDATABASE", "PGUSER", "PGPASSWORD"]
    missing = [n for n in required if not os.getenv(n)]
    if missing:
        raise RuntimeError(f"Missing DB config: {', '.join(missing)}")
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


QUERIES: list[tuple[str, str]] = [
    ("rag_sources_count", "select count(*)::bigint as n from rag_sources"),
    ("rag_chunks_count", "select count(*)::bigint as n from rag_chunks"),
    (
        "rag_sources_by_status",
        "select status, count(*)::bigint as n from rag_sources group by status order by n desc",
    ),
    (
        "rag_sources_by_category",
        "select category, count(*)::bigint as n from rag_sources group by category order by n desc",
    ),
    (
        "rag_sources_by_file_ext",
        "select file_ext, count(*)::bigint as n from rag_sources group by file_ext order by n desc",
    ),
    (
        "rag_chunks_by_embedding_dim",
        "select embedding_dim, count(*)::bigint as n from rag_chunks group by embedding_dim order by n desc",
    ),
    (
        "empty_chunks",
        "select count(*)::bigint as n from rag_chunks where content is null or length(trim(content)) = 0",
    ),
    (
        "recent_sources",
        """
        select id, title, category, url, file_ext, source_type, status, updated_at
        from rag_sources
        order by updated_at desc
        limit 10
        """,
    ),
    (
        "recent_chunks_excerpt",
        """
        select s.title, s.category, s.url, s.file_ext, c.chunk_index,
               left(c.content, 300) as excerpt
        from rag_sources s
        join rag_chunks c on c.source_id = s.id
        order by s.updated_at desc
        limit 10
        """,
    ),
    (
        "duplicate_title_url",
        """
        select title, url, count(*)::bigint as n
        from rag_sources
        group by title, url
        having count(*) > 1
        order by n desc
        limit 20
        """,
    ),
    (
        "json_vs_attachment_hint",
        """
        select
          count(*) filter (where file_ext = 'json')::bigint as json_ext_sources,
          count(*) filter (where file_ext in ('pdf','hwp','hwpx','docx','doc'))::bigint as attachment_ext_sources,
          count(*) filter (where source_type = 'web')::bigint as web_sources,
          count(*) filter (where metadata->>'doc_url' is not null and metadata->>'doc_url' <> '')::bigint as has_doc_url_meta
        from rag_sources
        """,
    ),
]


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    out: dict[str, object] = {}
    try:
        with connect() as conn:
            with conn.cursor() as cur:
                for key, sql in QUERIES:
                    cur.execute(sql)
                    rows = cur.fetchall()
                    if len(rows) == 1 and len(rows[0]) == 1:
                        out[key] = list(rows[0].values())[0]
                    else:
                        out[key] = rows
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()

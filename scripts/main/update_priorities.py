"""Update priority scores for PKNU notice and student_life RAG sources."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from dotenv import load_dotenv

try:
    import psycopg
    from psycopg import sql
    from psycopg.rows import dict_row
    from psycopg.types.json import Jsonb
except ModuleNotFoundError:
    psycopg = None
    sql = None
    dict_row = None

    class Jsonb:  # type: ignore[no-redef]
        def __init__(self, value: Any) -> None:
            self.value = value

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.rag.priority import build_rule_feature_set, calculate_main_priority  # noqa: E402

DATASET_TABLES = {
    "pknu_notice": {
        "sources": "pknu_notice_sources",
        "chunks": "pknu_notice_chunks",
        "index": PROJECT_ROOT / "files" / "pknu_notice" / "vectorized" / "index.jsonl",
    },
    "pknu_student_life": {
        "sources": "pknu_student_life_sources",
        "chunks": "pknu_student_life_chunks",
        "index": PROJECT_ROOT / "files" / "pknu_student_life" / "vectorized" / "index.jsonl",
    },
}
DEFAULT_RULE_INDEX = PROJECT_ROOT / "files" / "rule" / "vectorized" / "index.jsonl"
LOG_DIR = PROJECT_ROOT / "logs"
LOG_FILE = LOG_DIR / "update_main_priorities.log"

log = logging.getLogger(__name__)


def configure_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(LOG_FILE, encoding="utf-8"),
        ],
    )


def with_connect_timeout(conninfo: str, timeout_seconds: int = 10) -> str:
    parts = urlsplit(conninfo)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query.setdefault("connect_timeout", str(timeout_seconds))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def connect() -> Any:
    if psycopg is None or dict_row is None:
        raise RuntimeError("psycopg is required for database updates. Install backend requirements first.")

    load_dotenv(PROJECT_ROOT / "backend" / ".env")
    conninfo = os.getenv("DATABASE_URL") or os.getenv("SUPABASE_DB_URL")
    if conninfo:
        return psycopg.connect(
            with_connect_timeout(conninfo),
            row_factory=dict_row,
            prepare_threshold=None,
        )

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
        connect_timeout=10,
        row_factory=dict_row,
        prepare_threshold=None,
    )


def iter_index_records(index_path: Path) -> Iterable[dict[str, Any]]:
    with index_path.open("r", encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON on line {line_number}: {exc}") from exc


def aggregate_source_records(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    sources: dict[str, dict[str, Any]] = {}
    for record in records:
        metadata = record.get("metadata") or {}
        if not isinstance(metadata, dict):
            metadata = {}
        source_slug = str(record.get("source_slug") or metadata.get("source_slug") or "")
        if not source_slug:
            continue
        source = sources.setdefault(
            source_slug,
            {
                "id": source_slug,
                "title": metadata.get("doc_title") or metadata.get("source_file") or source_slug,
                "metadata": metadata,
                "content_parts": [],
            },
        )
        source["content_parts"].append(str(record.get("text") or record.get("content") or ""))
    return [
        {
            "id": source["id"],
            "title": source["title"],
            "metadata": source["metadata"],
            "content": "\n\n".join(source["content_parts"]),
        }
        for source in sources.values()
    ]


def fetch_rule_contents(conn: Any) -> list[str]:
    query = """
        select c.content
        from public.rule_chunks as c
        join public.rule_sources as s on s.id = c.source_id
        where s.status = 'active'
    """
    with conn.cursor() as cur:
        cur.execute(query)
        return [str(row["content"] or "") for row in cur.fetchall()]


def fetch_documents(conn: Any, dataset: str) -> list[dict[str, Any]]:
    if sql is None:
        raise RuntimeError("psycopg is required for database updates. Install backend requirements first.")
    tables = DATASET_TABLES[dataset]
    query = sql.SQL("""
        select
            s.id,
            coalesce(s.title, s.source_slug) as title,
            s.metadata,
            coalesce(string_agg(c.content, E'\n\n' order by c.chunk_index), '') as content
        from public.{sources_table} as s
        left join public.{chunks_table} as c on c.source_id = s.id
        where s.status = 'active'
        group by s.id, s.title, s.source_slug, s.metadata
    """).format(
        sources_table=sql.Identifier(tables["sources"]),
        chunks_table=sql.Identifier(tables["chunks"]),
    )
    with conn.cursor() as cur:
        cur.execute(query)
        return list(cur.fetchall())


def build_updates(
    dataset: str,
    documents: Iterable[dict[str, Any]],
    rule_features: set[str],
) -> list[dict[str, Any]]:
    updates: list[dict[str, Any]] = []
    for doc in documents:
        metadata = doc.get("metadata") or {}
        if not isinstance(metadata, dict):
            metadata = {}
        score, details = calculate_main_priority(
            dataset=dataset,
            content=str(doc.get("content") or ""),
            rule_features=rule_features,
            metadata=metadata,
            title=str(doc.get("title") or ""),
        )
        updates.append(
            {
                "id": doc["id"],
                "title": doc.get("title") or "",
                "priority_score": score,
                "priority_details": Jsonb(details),
            }
        )
    return updates


def log_preview(dataset: str, updates: list[dict[str, Any]], limit: int) -> None:
    for row in sorted(updates, key=lambda item: item["priority_score"], reverse=True)[:limit]:
        log.info("[%s PREVIEW] %.4f %s %s", dataset, row["priority_score"], row["id"], row["title"])


def update_dataset_priorities(
    conn: Any,
    dataset: str,
    rule_features: set[str],
    dry_run: bool,
    preview_limit: int,
) -> int:
    if sql is None:
        raise RuntimeError("psycopg is required for database updates. Install backend requirements first.")
    documents = fetch_documents(conn, dataset)
    updates = build_updates(dataset, documents, rule_features)
    log.info("loaded %s sources=%d", dataset, len(updates))

    if dry_run:
        log_preview(dataset, updates, preview_limit)
        return len(updates)

    sources_table = DATASET_TABLES[dataset]["sources"]
    update_sql = sql.SQL("""
        update public.{sources_table}
        set
            priority_score = %(priority_score)s,
            priority_details = %(priority_details)s,
            priority_updated_at = now()
        where id = %(id)s
    """).format(sources_table=sql.Identifier(sources_table))
    with conn.cursor() as cur:
        cur.executemany(update_sql, updates)
    conn.commit()
    return len(updates)


def preview_from_index(dataset: str, index_path: Path, rule_index_path: Path, preview_limit: int) -> int:
    documents = aggregate_source_records(iter_index_records(index_path))
    rule_documents = aggregate_source_records(iter_index_records(rule_index_path))
    rule_features = build_rule_feature_set(str(doc.get("content") or "") for doc in rule_documents)
    updates = build_updates(dataset, documents, rule_features)
    log.info(
        "loaded local dataset=%s sources=%d rule_sources=%d rule_features=%d",
        dataset,
        len(updates),
        len(rule_documents),
        len(rule_features),
    )
    log_preview(dataset, updates, preview_limit)
    return len(updates)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Update PKNU notice/student_life priority scores.")
    parser.add_argument(
        "--dataset",
        choices=["pknu_notice", "pknu_student_life", "all"],
        default="all",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--from-index",
        action="store_true",
        help="Preview from local vectorized index files. Requires --dry-run.",
    )
    parser.add_argument("--index-path", type=Path, default=None)
    parser.add_argument("--rule-index-path", type=Path, default=DEFAULT_RULE_INDEX)
    parser.add_argument("--preview-limit", type=int, default=10)
    return parser.parse_args()


def main() -> None:
    configure_logging()
    args = parse_args()
    if args.preview_limit <= 0:
        raise ValueError("--preview-limit must be positive")
    datasets = list(DATASET_TABLES) if args.dataset == "all" else [args.dataset]

    if args.from_index:
        if not args.dry_run:
            raise ValueError("--from-index is preview-only; pass --dry-run")
        if len(datasets) != 1 and args.index_path:
            raise ValueError("--index-path can only be used with a single --dataset")
        total = 0
        for dataset in datasets:
            index_path = args.index_path or DATASET_TABLES[dataset]["index"]
            total += preview_from_index(dataset, index_path, args.rule_index_path, args.preview_limit)
    else:
        with connect() as conn:
            rule_contents = fetch_rule_contents(conn)
            rule_features = build_rule_feature_set(rule_contents)
            log.info("loaded rule_chunks=%d rule_features=%d", len(rule_contents), len(rule_features))
            total = 0
            for dataset in datasets:
                total += update_dataset_priorities(conn, dataset, rule_features, args.dry_run, args.preview_limit)

    log.info("done: processed %d sources", total)


if __name__ == "__main__":
    main()

"""Fill CE document priority scores from CE/rule content overlap."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from dotenv import load_dotenv

try:
    import psycopg
    from psycopg.rows import dict_row
    from psycopg.types.json import Jsonb
except ModuleNotFoundError:
    psycopg = None
    dict_row = None

    class Jsonb:  # type: ignore[no-redef]
        def __init__(self, value: Any) -> None:
            self.value = value

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.ce.priority import (  # noqa: E402
    build_rule_feature_set,
    calculate_ce_priority,
)

LOG_DIR = PROJECT_ROOT / "logs"
LOG_FILE = LOG_DIR / "update_ce_priorities.log"

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


def fetch_ce_documents(conn: Any) -> list[dict[str, Any]]:
    query = """
        select
            s.id,
            s.title,
            s.metadata,
            coalesce(string_agg(c.content, E'\n\n' order by c.chunk_index), '') as content
        from public.rag_sources as s
        left join public.rag_chunks as c on c.source_id = s.id
        where s.status = 'active'
        group by s.id
    """
    with conn.cursor() as cur:
        cur.execute(query)
        return list(cur.fetchall())


def update_ce_priorities(conn: Any, dry_run: bool) -> int:
    rule_contents = fetch_rule_contents(conn)
    rule_features = build_rule_feature_set(rule_contents)
    ce_documents = fetch_ce_documents(conn)
    log.info(
        "loaded rule_chunks=%d rule_features=%d ce_sources=%d",
        len(rule_contents),
        len(rule_features),
        len(ce_documents),
    )

    updates: list[dict[str, Any]] = []
    for doc in ce_documents:
        metadata = doc.get("metadata") or {}
        if not isinstance(metadata, dict):
            metadata = {}
        score, details = calculate_ce_priority(
            str(doc["content"] or ""),
            rule_features,
            metadata=metadata,
            title=str(doc.get("title") or ""),
        )
        updates.append(
            {
                "id": doc["id"],
                "priority_score": score,
                "priority_details": Jsonb(details),
            }
        )

    if dry_run:
        preview = sorted(updates, key=lambda row: row["priority_score"], reverse=True)[:10]
        for row in preview:
            log.info("[DRY-RUN] %s priority_score=%.4f", row["id"], row["priority_score"])
        return len(updates)

    update_sql = """
        update public.rag_sources
        set
            priority_score = %(priority_score)s,
            priority_details = %(priority_details)s,
            priority_updated_at = now()
        where id = %(id)s
    """
    with conn.cursor() as cur:
        cur.executemany(update_sql, updates)
    conn.commit()
    return len(updates)


def main() -> None:
    configure_logging()
    parser = argparse.ArgumentParser(description="Update CE document priority scores.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    with connect() as conn:
        count = update_ce_priorities(conn, args.dry_run)
    log.info("done: processed %d CE source priorities", count)


if __name__ == "__main__":
    main()

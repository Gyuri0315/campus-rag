"""
[rule 문서의 우선순위 점수를 계산하고 DB에 반영하는 스크립트, update_priorities.py]

rule 문서 1개(source)마다 우선 순위 점수를 계산해서 public.rule_sources.priority_* 컬럼에 저장한다. 
이 점수는 RAG 검색에서 문서 랭킹을 보정하기 위한 보조 신호이며, embedding similarity를 대체하는 절대 점수가 아니다.

기본 실행:
    - public.rule_sources + public.rule_chunks에서 active rule 문서를 읽는다.
    - 각 source별 priority_score와 priority_details를 계산한다.
    - rule_sources.priority_score, priority_details, priority_updated_at을 갱신한다..

현재 우선순위 계산식은 scripts/rule/priority.py에 구현되어 있다.
    priority_score =
        authority band + band_span * (
            0.50 * recency_score
          + 0.35 * tree_score
          + 0.15 * source_quality_score
        )

점수 기준:
    - authority_score          : 학칙 > 규정 > 세칙/지침 순서가 뒤집히지 않도록 점수 구간을 나눈다.
    - recency_score            : 학칙/규정은 시행일, 세칙/지침은 개정일이 최근일수록 높게 평가한다.
    - tree_score               : 학칙 트리의 kind_type, depth, path 정보를 반영한다.
    - source_quality_score     : 제목, URL, 본문, 원본 첨부파일 메타데이터가 충분한 문서를 높게 평가한다.
"""

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

from scripts.rule.priority import aggregate_source_records, calculate_rule_priority  # noqa: E402

DEFAULT_INDEX_PATH = PROJECT_ROOT / "files" / "rule" / "vectorized" / "index.jsonl"
DEFAULT_TREE_PATH = PROJECT_ROOT / "files" / "rule" / "output" / "tree" / "rule_tree_nodes.json"
LOG_DIR = PROJECT_ROOT / "logs"
LOG_FILE = LOG_DIR / "update_rule_priorities.log"

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


def fetch_rule_documents(conn: Any) -> list[dict[str, Any]]:
    query = """
        select
            s.id,
            coalesce(s.title, s.source_slug) as title,
            s.metadata,
            coalesce(string_agg(c.content, E'\n\n' order by c.chunk_index), '') as content
        from public.rule_sources as s
        left join public.rule_chunks as c on c.source_id = s.id
        where s.status = 'active'
        group by s.id, s.title, s.source_slug, s.metadata
    """
    with conn.cursor() as cur:
        cur.execute(query)
        return list(cur.fetchall())


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


def normalize_key(value: object) -> str:
    return " ".join(str(value or "").replace("\u00a0", " ").split()).lower()


def load_tree_index(tree_path: Path) -> dict[str, dict[str, dict[str, Any]]]:
    index: dict[str, dict[str, dict[str, Any]]] = {
        "by_lid": {},
        "by_title": {},
    }
    if not tree_path.exists():
        log.warning("rule tree priority file not found: %s", tree_path)
        return index

    payload = json.loads(tree_path.read_text(encoding="utf-8-sig"))
    nodes = payload.get("nodes") if isinstance(payload, dict) else None
    if not isinstance(nodes, list):
        log.warning("rule tree priority file has no nodes list: %s", tree_path)
        return index

    for node in nodes:
        if not isinstance(node, dict):
            continue
        lid = normalize_key(node.get("lid"))
        title = normalize_key(node.get("title"))
        if lid:
            index["by_lid"][lid] = node
        if title:
            index["by_title"][title] = node
    log.info("loaded rule_tree_nodes=%d from %s", len(nodes), tree_path)
    return index


def find_tree_info(doc: dict[str, Any], tree_index: dict[str, dict[str, dict[str, Any]]]) -> dict[str, Any] | None:
    metadata = doc.get("metadata") or {}
    if not isinstance(metadata, dict):
        metadata = {}

    for candidate in (
        metadata.get("source_id"),
        metadata.get("rule_source_id"),
        metadata.get("lid"),
    ):
        node = tree_index["by_lid"].get(normalize_key(candidate))
        if node:
            return node

    for candidate in (
        doc.get("title"),
        metadata.get("doc_title"),
        metadata.get("source_file"),
    ):
        node = tree_index["by_title"].get(normalize_key(candidate))
        if node:
            return node
    return None


def build_updates(
    documents: Iterable[dict[str, Any]],
    tree_index: dict[str, dict[str, dict[str, Any]]],
) -> list[dict[str, Any]]:
    updates: list[dict[str, Any]] = []
    for doc in documents:
        metadata = doc.get("metadata") or {}
        if not isinstance(metadata, dict):
            metadata = {}
        tree_info = find_tree_info(doc, tree_index)
        score, details = calculate_rule_priority(
            title=str(doc.get("title") or ""),
            content=str(doc.get("content") or ""),
            metadata=metadata,
            tree_info=tree_info,
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


def log_preview(updates: list[dict[str, Any]], limit: int) -> None:
    for row in sorted(updates, key=lambda item: item["priority_score"], reverse=True)[:limit]:
        log.info("[PREVIEW] %.4f %s %s", row["priority_score"], row["id"], row["title"])


def update_rule_priorities(conn: Any, dry_run: bool, preview_limit: int, tree_path: Path) -> int:
    documents = fetch_rule_documents(conn)
    tree_index = load_tree_index(tree_path)
    updates = build_updates(documents, tree_index)
    log.info("loaded rule_sources=%d", len(updates))

    if dry_run:
        log_preview(updates, preview_limit)
        return len(updates)

    update_sql = """
        update public.rule_sources
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


def preview_from_index(index_path: Path, preview_limit: int, tree_path: Path) -> int:
    documents = aggregate_source_records(iter_index_records(index_path))
    tree_index = load_tree_index(tree_path)
    updates = build_updates(documents, tree_index)
    log.info("loaded local rule_sources=%d from %s", len(updates), index_path)
    log_preview(updates, preview_limit)
    return len(updates)


def main() -> None:
    configure_logging()
    parser = argparse.ArgumentParser(description="Update rule document priority scores.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--from-index",
        action="store_true",
        help="Read local files/rule/vectorized/index.jsonl for preview. Requires --dry-run.",
    )
    parser.add_argument("--index-path", type=Path, default=DEFAULT_INDEX_PATH)
    parser.add_argument(
        "--tree-path",
        type=Path,
        default=DEFAULT_TREE_PATH,
        help="Path to files/rule/output/tree/rule_tree_nodes.json for tree-aware priority scoring.",
    )
    parser.add_argument("--preview-limit", type=int, default=20)
    args = parser.parse_args()

    if args.preview_limit <= 0:
        raise ValueError("--preview-limit must be positive")
    if args.from_index:
        if not args.dry_run:
            raise ValueError("--from-index is preview-only; pass --dry-run")
        count = preview_from_index(args.index_path, args.preview_limit, args.tree_path)
    else:
        with connect() as conn:
            count = update_rule_priorities(conn, args.dry_run, args.preview_limit, args.tree_path)

    log.info("done: processed %d rule source priorities", count)


if __name__ == "__main__":
    main()

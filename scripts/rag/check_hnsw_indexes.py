"""Verify HNSW indexes exist for all *_chunks tables (P0-1)."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.db import connect_postgres  # noqa: E402

CHUNKS_TABLES = (
    "rag_chunks",
    "rule_chunks",
    "pknu_notice_chunks",
    "pknu_student_life_chunks",
)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    missing: list[str] = []
    with connect_postgres(autocommit=True) as conn:
        with conn.cursor() as cur:
            for table in CHUNKS_TABLES:
                cur.execute(
                    """
                    select indexname, indexdef
                    from pg_indexes
                    where schemaname = 'public'
                      and tablename = %s
                      and indexdef ilike '%%hnsw%%'
                    """,
                    (table,),
                )
                rows = cur.fetchall()
                cur.execute(
                    f"select count(*) from public.{table}"  # noqa: S608 (table from allowlist)
                )
                (row_count,) = cur.fetchone()
                if rows:
                    for indexname, indexdef in rows:
                        print(f"[OK]   {table:32s} rows={row_count:>7} index={indexname}")
                        print(f"       {indexdef}")
                else:
                    missing.append(table)
                    print(f"[MISS] {table:32s} rows={row_count:>7} -- NO HNSW INDEX")
    if missing:
        print(f"\nMissing HNSW index on: {', '.join(missing)}")
        return 1
    print("\nAll 4 chunks tables have HNSW indexes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

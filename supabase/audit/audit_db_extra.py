"""Extra read-only DB checks."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import psycopg
from dotenv import load_dotenv
from psycopg.rows import dict_row

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    load_dotenv(PROJECT_ROOT / "backend" / ".env")
    conninfo = os.environ["DATABASE_URL"]
    queries = {
        "ce_url_sources": "select count(*)::int as n from rag_sources where url like 'https://ce.pknu.ac.kr%'",
        "pknu_main_sources": "select count(*)::int as n from rag_sources where url like 'https://www.pknu.ac.kr/main/163%'",
        "short_content_chunks": "select count(*)::int as n from rag_chunks where length(content) < 80",
        "ce_graduation_chunks": """
            select s.title, s.url, length(c.content) as len, left(c.content, 200) as ex
            from rag_sources s join rag_chunks c on c.source_id = s.id
            where s.url = 'https://ce.pknu.ac.kr/ce/2889'
            order by c.chunk_index limit 5
        """,
        "scholarship_samples": """
            select s.title, s.url, left(c.content, 200) as ex
            from rag_sources s join rag_chunks c on c.source_id = s.id
            where s.category = '등록·장학'
              and (s.title ilike '%장학%' or c.content ilike '%장학%')
            limit 5
        """,
    }
    out = {}
    with psycopg.connect(conninfo, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            for key, sql in queries.items():
                cur.execute(sql)
                out[key] = cur.fetchall()
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()

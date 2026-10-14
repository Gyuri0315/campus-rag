"""_opening_chunks_of_current_notices: add a current-term notice's opening chunks."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app import retrieval


class FakeQuery:
    def __init__(self, rows, log):
        self.rows, self.log = rows, log

    def select(self, *_):
        return self

    def in_(self, column, values):
        self.log.append(("in", column, tuple(values)))
        self.rows = [r for r in self.rows if r[column] in values]
        return self

    def lt(self, column, value):
        self.rows = [r for r in self.rows if r[column] < value]
        return self

    def execute(self):
        return SimpleNamespace(data=self.rows)


class FakeClient:
    def __init__(self, chunks):
        self.chunks, self.log = chunks, []

    def table(self, name):
        self.log.append(("table", name))
        return FakeQuery(list(self.chunks.get(name, [])), self.log)


def candidate(source_id, title, content, rpc="match_rag_documents", chunk_index=4):
    return {
        "id": f"{source_id}-{chunk_index}", "source_id": source_id, "title": title, "url": f"https://x/{source_id}",
        "chunk_index": chunk_index, "content": content, "similarity": 0.6,
        "metadata": {"doc_title": title, "rpc_name": rpc},
    }


LONG = " 학부생 대상 수강신청 일정과 유의사항을 안내하오니 기간 내에 학사행정정보시스템에서 신청하시기 바랍니다."


class OpeningChunksTests(unittest.TestCase):
    def run_expand(self, candidates, chunks):
        client = FakeClient(chunks)
        with patch("app.freshness.today_kst", return_value=__import__("datetime").date(2026, 10, 14)):
            extra = retrieval._opening_chunks_of_current_notices(
                client, candidates, "수강신청 기간", [], 0.3, 0.15, 0.1)
        return client, extra

    def test_adds_missing_opening_chunks_of_current_term_notice(self):
        current = candidate("s1", "2026-2학기 학부 본수강신청 안내", "학점 이월 규정" + LONG)
        chunks = {"rag_chunks": [
            {"id": "s1-0", "source_id": "s1", "chunk_id": "c0", "chunk_index": 0, "content": "수강신청 8. 5.(수)~8. 7.(금)" + LONG, "metadata": {}},
            {"id": "s1-1", "source_id": "s1", "chunk_id": "c1", "chunk_index": 1, "content": "수강 취소 9. 21.~9. 29." + LONG, "metadata": {}},
            {"id": "s1-5", "source_id": "s1", "chunk_id": "c5", "chunk_index": 5, "content": "기타", "metadata": {}},
        ]}
        _client, extra = self.run_expand([current], chunks)
        self.assertEqual([0, 1], sorted(r["chunk_index"] for r in extra))
        self.assertTrue(all(r["title"] == current["title"] and r["similarity"] == 0.6 for r in extra))

    def test_ignores_past_terms_and_non_notice_tables(self):
        old = candidate("s2", "2025-1학기 수강신청 안내", "x")
        rule = candidate("s3", "2026-2학기 운영 지침", "y", rpc="match_rule_documents")
        client, extra = self.run_expand([old, rule], {})
        self.assertEqual([], extra)
        self.assertEqual([], client.log)  # no fetch at all

    def test_chunk_already_in_pool_is_not_duplicated(self):
        current = candidate("s1", "2026-2학기 학부 본수강신청 안내", "수강신청 8. 5.(수)~8. 7.(금)" + LONG, chunk_index=0)
        chunks = {"rag_chunks": [
            {"id": "s1-0", "source_id": "s1", "chunk_id": "c0", "chunk_index": 0, "content": "수강신청 8. 5.(수)~8. 7.(금)" + LONG, "metadata": {}},
        ]}
        _client, extra = self.run_expand([current], chunks)
        self.assertEqual([], extra)


if __name__ == "__main__":
    unittest.main()

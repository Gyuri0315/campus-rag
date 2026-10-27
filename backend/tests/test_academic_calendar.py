"""_academic_calendar_chunks: schedule questions get the calendar rows they name."""

import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from app import retrieval

URL = "https://ce.pknu.ac.kr/ce/1814?action=view&no=9979918"
HEAD = "문서제목: 2026학년도 학사일정 안내\n분류: 공지사항\n첨부명: 학사일정(안).hwp\n본문: "
TAIL = " | 학사행정 일정은 사정에 따라 변경될 수 있으니 학부 공지를 함께 확인하시기 바랍니다."


class FakeQuery:
    def __init__(self, rows, log):
        self.rows, self.log = rows, log

    def select(self, *_):
        return self

    def eq(self, column, value):
        self.rows = [r for r in self.rows if r.get(column) == value]
        return self

    def ilike(self, column, pattern):
        parts = [p for p in pattern.split("%") if p]
        self.rows = [r for r in self.rows if all(p in r.get(column, "") for p in parts)]
        return self

    def in_(self, column, values):
        self.rows = [r for r in self.rows if r[column] in values]
        return self

    def limit(self, _):
        return self

    def execute(self):
        self.log.append("execute")
        return SimpleNamespace(data=self.rows)


class FakeClient:
    def __init__(self, tables):
        self.tables, self.log = tables, []

    def table(self, name):
        return FakeQuery(list(self.tables.get(name, [])), self.log)


def chunk(index, body):
    return {"id": f"c{index}", "source_id": "s1", "chunk_id": f"c{index}", "chunk_index": index,
            "content": HEAD + body + TAIL, "metadata": {}, "embedding": "[1,0]"}


TABLES = {
    "rag_sources": [
        {"id": "s1", "url": URL, "title": "2026학년도 학사일정 안내", "status": "active",
         "source_slug": "x", "source_type": "hwp", "parent_url": None, "metadata": {}, "priority_score": 0.5},
    ],
    "rag_chunks": [
        chunk(1, "2026학년도 학사일정(안) 학사내용 | 2026학년도(안) | 2025학년도 제1학기 개강 | 3. 3.(화) | 3. 4.(화)"),
        chunk(2, "제1학기 기말고사 | 6. 18.(목) ~ 6. 24.(수) | 6. 20.(금) ~ 6. 26.(목)"),
        chunk(3, "제2학기 휴·복학 | 7. 23.(목) ~ 7. 29.(수) | 7. 24.(목) ~ 7. 30.(수)"),
        chunk(4, "제2학기 기말고사 | 12. 14.(월) ~ 12. 20.(일) | 12. 15.(월) ~ 12. 21.(일)"),
    ],
}


class AcademicCalendarTests(unittest.TestCase):
    def setUp(self):
        retrieval._calendar_cache.clear()

    def run_calendar(self, question, client=None):
        client = client or FakeClient(TABLES)
        with patch("app.freshness.today_kst", return_value=date(2026, 10, 24)):
            extra = retrieval._academic_calendar_chunks(
                client, ["match_rag_documents"], [1.0, 0.0], [], question, [], 0.3, 0.15, 0.1)
        return client, extra

    def test_rows_naming_the_asked_event_are_added_with_the_table_header(self):
        _client, extra = self.run_calendar("2학기 기말고사 기간 언제야?")
        self.assertEqual({2, 4}, {row["chunk_index"] for row in extra})
        for row in extra:
            self.assertIn("표 열: 학사내용 | 2026학년도(안) | 2025학년도", row["content"])

    def test_next_years_first_term_is_read_from_this_years_calendar(self):
        tables = {**TABLES, "rag_chunks": [*TABLES["rag_chunks"],
            chunk(5, "제2학기 수강신청 변경 | 9. 1.(화) ~ 9. 7.(월) | 9. 1.(월) ~ 9. 5.(금)"),
            chunk(6, "2027학년도 제1학기 수강신청 | 2027. 2. 15.(월) ~ 2. 17.(수) | 2026. 2. 11.(수) ~ 2. 13.(금)"),
        ]}
        _client, extra = self.run_calendar("2027학년도 1학기 수강신청 기간 언제야?", FakeClient(tables))
        self.assertEqual(6, extra[0]["chunk_index"])

    def test_nothing_added_when_no_question_word_is_on_the_calendar(self):
        _client, extra = self.run_calendar("정효택 장학금 신청 기간")
        self.assertEqual([], extra)

    def test_calendar_is_fetched_once_per_year(self):
        client = FakeClient(TABLES)
        self.run_calendar("기말고사 기간", client)
        fetches = len(client.log)
        self.run_calendar("휴학 기간", client)
        self.assertEqual(fetches, len(client.log))


if __name__ == "__main__":
    unittest.main()

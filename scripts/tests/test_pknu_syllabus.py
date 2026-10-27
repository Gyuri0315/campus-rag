import json
import unittest
from pathlib import Path

from scripts.crawlers import pknu_syllabus as ps

ROOT = Path(__file__).resolve().parents[2]

RESPONSE = """<?xml version="1.0" encoding="UTF-8"?>
<Root xmlns="http://www.nexacroplatform.com/platform/dataset">
  <Parameters><Parameter id="ErrorCode" type="string">0</Parameter></Parameters>
  <Dataset id="output1"><ColumnInfo /><Rows>
    <Row><Col id="YY">2026</Col><Col id="DEPT_NM">컴퓨터공학전공</Col><Col id="UNIV_GRSC_CLSF">학부</Col>
      <Col id="SBJT_KOR_NM">자료구조</Col><Col id="COURSE_NO">104605</Col><Col id="DCLSS_NO">106</Col>
      <Col id="STAFF_NM">김영봉</Col><Col id="PNT_THEO_PRAC">3-2-2</Col><Col id="SBJT_FG_NM">전공공통</Col>
      <Col id="LSN_MTHD_FG">대면</Col><Col id="KOR_YN">Y</Col></Row>
    <Row><Col id="YY">2026</Col><Col id="DEPT_NM">컴퓨터공학전공</Col><Col id="UNIV_GRSC_CLSF">학부</Col>
      <Col id="SBJT_KOR_NM">자료구조</Col><Col id="COURSE_NO">104605</Col><Col id="DCLSS_NO">105</Col>
      <Col id="STAFF_NM">권오흠</Col><Col id="PNT_THEO_PRAC">3-2-2</Col><Col id="SBJT_FG_NM">전공공통</Col>
      <Col id="LSN_MTHD_FG">대면</Col><Col id="KOR_YN">Y</Col></Row>
    <Row><Col id="YY">2026</Col><Col id="DEPT_NM">기계공학전공</Col><Col id="UNIV_GRSC_CLSF">학부</Col>
      <Col id="SBJT_KOR_NM">열역학</Col><Col id="COURSE_NO">100001</Col><Col id="DCLSS_NO">101</Col>
      <Col id="STAFF_NM">홍길동</Col><Col id="PNT_THEO_PRAC">3-3-0</Col></Row>
    <Row><Col id="YY">2026</Col><Col id="UNIV_GRSC_CLSF">학부</Col>
      <Col id="SBJT_KOR_NM">창의적문제해결전략</Col><Col id="COURSE_NO">K00003</Col><Col id="DCLSS_NO">901</Col></Row>
    <Row><Col id="YY">2026</Col><Col id="DEPT_NM">컴퓨터공학과</Col><Col id="UNIV_GRSC_CLSF">일반대학원</Col>
      <Col id="SBJT_KOR_NM">고급알고리즘</Col><Col id="COURSE_NO">900001</Col><Col id="DCLSS_NO">101</Col></Row>
  </Rows></Dataset>
</Root>"""


class PknuSyllabusTests(unittest.TestCase):
    def test_parse_rows_and_error_response(self):
        rows = ps.parse_rows(RESPONSE)
        self.assertEqual(["106", "105", "101", "901", "101"], [row["DCLSS_NO"] for row in rows])
        with self.assertRaises(ValueError):
            ps.parse_rows('{"data":{},"dataset":[],"message":"null","code":"-1"}')

    def test_request_carries_term_code_and_escaped_name(self):
        body = ps.build_request(2026, "2", course_name="C&C++")
        self.assertIn('<Parameter id="SHTM_CD">U0003002</Parameter>', body)
        self.assertIn("C&amp;C++", body)

    def test_one_schema_document_per_course_with_sorted_sections(self):
        docs = ps.build_documents(ps.parse_rows(RESPONSE)[:2], 2026, "2")
        self.assertEqual(1, len(docs))
        doc = docs[0]
        schema = json.loads((ROOT / "schemas" / "crawler-document-1.0.schema.json").read_text(encoding="utf-8"))
        self.assertTrue(set(schema["required"]) <= set(doc))
        self.assertIn(doc["type"], schema["properties"]["type"]["enum"])
        content = doc["content"]
        self.assertIn("2026학년도 2학기 컴퓨터공학전공 개설 과목: 자료구조", content)
        self.assertIn("3학점(이론 2시간, 실습 2시간)", content)
        self.assertLess(content.index("105분반: 권오흠"), content.index("106분반: 김영봉"))

    def test_one_empty_search_covers_every_department(self):
        session = FakeSession(RESPONSE)
        sections = ps.collect_sections(2026, "2", session=session)
        self.assertEqual(1, len(session.bodies))
        self.assertIn('<Parameter id="STAFF_NM"></Parameter>', session.bodies[0])
        self.assertEqual(
            ["기계공학전공", ps.NO_DEPARTMENT, "컴퓨터공학전공", "컴퓨터공학전공"],
            [row["DEPT_NM"] for row in sections],
        )  # graduate-school section left out


class FakeSession:
    def __init__(self, text):
        self.text = text
        self.headers = {}
        self.bodies = []

    def post(self, url, data, timeout):
        self.bodies.append(data.decode("utf-8"))
        return self

    def raise_for_status(self):
        pass


if __name__ == "__main__":
    unittest.main()

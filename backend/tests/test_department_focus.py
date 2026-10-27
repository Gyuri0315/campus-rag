"""department_focus: keep answers to the department a question names."""

import json
import tempfile
import unittest
from pathlib import Path

from app import department_focus as df

TABLE = ("문서제목: 졸업자격인증제 실시 현황\n본문: 0학번 이후 신입생 | 졸업작품 "
         "116 | 조형학부 | 공업디자인전공 | 실시 | TOEIC 700 "
         "117 | 정보융합대학 | 컴퓨터·인공지능공학부 | 컴퓨터공학전공 | 미실시 "
         "118 | 정보융합대학 | 디지털금융학과 | 미실시 "
         "119 | 미래융합학부 | 사회복지서비스학전공 | 실시 | TOEIC 700")


class RegistryNamesTests(unittest.TestCase):
    def test_names_come_from_the_registry(self):
        self.assertIn("컴퓨터공학전공", df.DEPARTMENT_NAMES)  # from "컴퓨터공학전공, 인공지능전공"
        self.assertIn("인공지능전공", df.DEPARTMENT_NAMES)
        self.assertNotIn("공과대학", df.DEPARTMENT_NAMES)

    def test_a_department_added_to_the_registry_is_picked_up(self):
        registry = {"departments": [
            {"name": "AI대학", "department": None, "majors": []},
            {"name": "새로운학과", "department": "새로운학과", "majors": ["가전공, 나전공"]},
            {"name": "공과대학 자유전공학부", "department": None, "majors": []},
        ]}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "registry.json"
            path.write_text(json.dumps(registry, ensure_ascii=False), encoding="utf-8")
            self.assertEqual(("가전공", "나전공", "새로운학과"), df.load_department_names(path))

    def test_missing_registry_leaves_only_aliases(self):
        self.assertEqual((), df.load_department_names(None))


class FocusVariantsTests(unittest.TestCase):
    def test_alias_and_registry_names(self):
        self.assertIn("컴퓨터공학", df.focus_variants("컴공으로 전과하려면?"))
        self.assertIn("기계공학", df.focus_variants("기계공학부 졸업요건 알려줘"))

    def test_no_department_named(self):
        self.assertIsNone(df.focus_variants("휴학 신청 기간 언제야?"))


class TableSliceTests(unittest.TestCase):
    def test_other_departments_slice_is_detected_even_with_spaced_names(self):
        variants = df.focus_variants("컴공 전과")
        self.assertTrue(df.is_other_departments_table("국어국문학과 1. 전입 사학과 영어영문학부 2.50 이상", variants))
        self.assertFalse(df.is_other_departments_table("컴퓨터·인공 지능공학부 국어국문학과 사학과 영어영문학부", variants))

    def test_mixed_slice_keeps_only_the_asked_rows(self):
        trimmed = df.keep_own_rows(TABLE, df.focus_variants("26학번 컴공 졸업인증"))
        self.assertIn("117 | 정보융합대학 | 컴퓨터·인공지능공학부", trimmed)
        self.assertNotIn("TOEIC 700", trimmed)
        self.assertNotIn("0학번 이후 신입생", trimmed)  # tail of a cut-off row from another department
        self.assertTrue(trimmed.startswith("문서제목: 졸업자격인증제 실시 현황\n본문:"))

    def test_plain_text_is_left_alone(self):
        self.assertIsNone(df.keep_own_rows("문서제목: 공지\n본문: 컴퓨터·인공지능공학부 학생은 신청하세요.", df.focus_variants("컴공")))


if __name__ == "__main__":
    unittest.main()

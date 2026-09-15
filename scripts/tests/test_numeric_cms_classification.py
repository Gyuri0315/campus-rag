from __future__ import annotations

import unittest
from pathlib import Path

from bs4 import BeautifulSoup

from scripts.crawlers.departments.adapters import get_adapter
from scripts.crawlers.departments.discovery import analyze_section_html


FIXTURES = Path(__file__).with_name("fixtures") / "departments"


class NumericCMSClassificationTests(unittest.TestCase):
    def _analyze(self, fixture: str, name: str):
        return analyze_section_html(
            name=name,
            page_url="https://department.example.test/dept/1234",
            html=(FIXTURES / fixture).read_text(encoding="utf-8"),
        )

    def test_view_link_collections_are_static_pages(self) -> None:
        cases = (
            ("faculty_page.html", "교수진"),
            ("member_directory.html", "구성원"),
            ("profile_list.html", "프로필"),
            ("static_gallery.html", "갤러리"),
            ("static_link_list.html", "교육과정"),
        )
        for fixture, name in cases:
            with self.subTest(fixture=fixture):
                section = self._analyze(fixture, name)
                self.assertEqual("static_page", section.kind)
                self.assertEqual("guide", section.document_type)
                self.assertEqual("candidate", section.status)
                self.assertIsNone(section.bbs_id)
                self.assertEqual([], section.warnings)

    def test_real_board_without_bbs_id_requires_an_adapter(self) -> None:
        section = self._analyze("board_without_bbs_id.html", "소식")
        self.assertEqual("board", section.kind)
        self.assertEqual("notice", section.document_type)
        self.assertEqual("requires_adapter", section.status)
        self.assertIsNone(section.bbs_id)
        self.assertIn("adapter required", section.warnings[0])

    def test_list_variants_use_action_view_document_ids(self) -> None:
        adapter = get_adapter("numeric_cms")
        cases = (
            ("numeric_cms/list_table_variant.html", ["pinned-A", "9994213"], [None, 270]),
            ("numeric_cms/list_card_variant.html", ["28554", "28343"], [None, None]),
            ("numeric_cms/list_gallery_variant.html", ["9983255", "9983254"], [None, None]),
        )
        for fixture, source_ids, post_numbers in cases:
            with self.subTest(fixture=fixture):
                soup = BeautifulSoup((FIXTURES / fixture).read_text(encoding="utf-8"), "lxml")
                items = adapter.parse_list(soup, "https://department.example.test/dept/100")
                self.assertEqual(source_ids, [item["source_id"] for item in items])
                self.assertEqual(post_numbers, [item["post_no"] for item in items])

    def test_navigation_detail_link_is_not_a_list_item(self) -> None:
        adapter = get_adapter("numeric_cms")
        soup = BeautifulSoup(
            (FIXTURES / "numeric_cms/list_table_variant.html").read_text(encoding="utf-8"),
            "lxml",
        )
        items = adapter.parse_list(soup, "https://department.example.test/dept/100")
        self.assertNotIn("9990000", {item["source_id"] for item in items})


if __name__ == "__main__":
    unittest.main()

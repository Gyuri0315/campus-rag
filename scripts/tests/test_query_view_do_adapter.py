from __future__ import annotations

import unittest
import tempfile
from pathlib import Path

from bs4 import BeautifulSoup

from scripts.crawlers.departments.adapters import get_adapter
from scripts.crawlers.departments.discovery import discover_from_html
from scripts.crawlers.common.schema import (
    apply_common_schema, attachment_error, build_attachment, validate_common_document,
)


ROOT = Path(__file__).with_name("fixtures") / "departments" / "fishsci"
BASE_URL = "https://fishsci.pknu.ac.kr/"


class QueryViewDoAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.adapter = get_adapter("query_view_do")
        cls.homepage = (ROOT / "homepage.html").read_text(encoding="utf-8")
        cls.board = (ROOT / "board.html").read_text(encoding="utf-8")

    def test_menu_discovery_uses_no_and_excludes_external_home_and_details(self) -> None:
        html = self.homepage.replace(
            "</nav>", '<a href="/kor/view.do?no=44&pgMode=View&idx=1262">상세</a>'
            '<a href="/kor/view.do?no=sitemap">SITEMAP</a>'
            '<a href="/kor/login.do">로그인</a></nav>',
        )
        menus = self.adapter.discover_menus(html, BASE_URL)
        self.assertEqual(["2", "44", "45", "46"], [menu["menu_no"] for menu in menus])
        self.assertTrue(all("pgMode" not in menu["url"] for menu in menus))

    def test_section_id_comes_from_menu_no(self) -> None:
        result = discover_from_html(
            site_key="fishsci.pknu.ac.kr", base_url=BASE_URL,
            homepage_html=self.homepage,
            section_html={"https://fishsci.pknu.ac.kr/kor/view.do?no=44": self.board},
            adapter_name="query_view_do",
        )
        notice = next(section for section in result.sections if section.id == "menu_44")
        self.assertEqual("board", notice.kind)
        self.assertIsNone(notice.bbs_id)
        self.assertEqual("/kor/view.do?no=44", notice.path)

    def test_list_uses_idx_as_source_id(self) -> None:
        items = self.adapter.parse_list(BeautifulSoup(self.board, "lxml"), BASE_URL + "kor/view.do?no=44")
        self.assertEqual(["1262", "1257"], [item["source_id"] for item in items])
        self.assertIn("idx=1262", items[0]["post_url"])

    def test_numeric_adapter_remains_separate(self) -> None:
        self.assertEqual("numeric_cms", get_adapter("numeric_cms").name)
        self.assertEqual("query_view_do", self.adapter.name)

    def test_detail_extracts_canonical_fields_and_absolute_attachment(self) -> None:
        html = (ROOT / "detail_with_attachment.html").read_text(encoding="utf-8")
        url = BASE_URL + "kor/view.do?no=44&pgMode=View&idx=1262"
        parsed = self.adapter.parse_detail(
            BeautifulSoup(html, "lxml"), url, {"source_id": "1262", "is_notice": True},
            base_url=BASE_URL, site_prefix="kor",
        )
        self.assertEqual("fixture 첨부 공지", parsed["title"])
        self.assertEqual("관리자", parsed["author"])
        self.assertEqual("2026-07-23", parsed["date"])
        self.assertEqual("1262", parsed["source_id"])
        self.assertEqual("첨부파일이 있는 공지사항의 정제된 본문입니다.", parsed["body"])
        self.assertEqual("https://fishsci.pknu.ac.kr/boardDown.do?fKey=878", parsed["attachments"][0]["url"])

    def test_empty_body_removes_repeated_ui_without_failing(self) -> None:
        html = (ROOT / "empty_detail.html").read_text(encoding="utf-8")
        parsed = self.adapter.parse_detail(
            BeautifulSoup(html, "lxml"), BASE_URL + "kor/view.do?no=44&pgMode=View&idx=1300",
            {"source_id": "1300"}, base_url=BASE_URL, site_prefix="kor",
        )
        self.assertIsNotNone(parsed)
        self.assertEqual("", parsed["body"])

    def test_attachment_failure_remains_a_valid_document(self) -> None:
        html = (ROOT / "detail_with_attachment.html").read_text(encoding="utf-8")
        url = BASE_URL + "kor/view.do?no=44&pgMode=View&idx=1262"
        parsed = self.adapter.parse_detail(
            BeautifulSoup(html, "lxml"), url, {"source_id": "1262"},
            base_url=BASE_URL, site_prefix="kor",
        )
        with tempfile.TemporaryDirectory() as directory:
            failed = build_attachment(
                index=1, name=parsed["attachments"][0]["name"],
                url=parsed["attachments"][0]["url"], project_root=Path(directory),
                error=attachment_error("REQUEST_FAILED", "fixture failure", True),
            )
            document = apply_common_schema(
                {"title": parsed["title"], "url": url, "category": "공지사항",
                 "subcategory": "공지사항", "content": parsed["body"], "attachments": [failed]},
                source_dataset="fishsci", source_id=parsed["source_id"], source_site=BASE_URL,
                document_type="notice", content_source="query_view_do",
                published_at=parsed["date"], author=parsed["author"],
            )
            self.assertEqual([], validate_common_document(document, Path(directory)))
        self.assertEqual("fishsci:1262", document["id"])
        self.assertEqual("1262", document["source_id"])
        self.assertEqual("2026-07-23T00:00:00+09:00", document["published_at"])
        self.assertFalse(document["attachments"][0]["downloaded"])
        self.assertEqual("REQUEST_FAILED", document["attachments"][0]["error"]["code"])


if __name__ == "__main__":
    unittest.main()

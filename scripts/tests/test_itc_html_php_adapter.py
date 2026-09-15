from pathlib import Path
import unittest
from bs4 import BeautifulSoup

from scripts.crawlers.departments.adapters import get_adapter
from scripts.crawlers.departments.discovery import discover_from_html

FIX = Path(__file__).parent / "fixtures" / "departments" / "itc"
BASE = "https://itc.pknu.ac.kr/html/00_main/"


class ItcHtmlPHPAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.adapter = get_adapter("html_php")
        cls.home = (FIX / "homepage.html").read_text(encoding="utf-8")
        cls.board = (FIX / "board.html").read_text(encoding="utf-8")
        cls.detail = (FIX / "detail.html").read_text(encoding="utf-8")
        cls.static = (FIX / "static.html").read_text(encoding="utf-8")

    def test_discovery_and_category_filter_identity(self):
        menus = self.adapter.discover_menus(self.home, BASE)
        self.assertEqual(4, len(menus))
        faculty = next(m for m in menus if m["name"] == "교수진")
        self.assertIn("category_idx=1", faculty["url"])
        html = {m["url"]: self.board if "/06/" in m["url"] else self.static for m in menus}
        result = discover_from_html(site_key="itc", base_url=BASE, homepage_html=self.home,
                                    section_html=html, adapter_name="html_php")
        self.assertEqual(4, len(result.sections))
        self.assertTrue(any(s.kind == "board" for s in result.sections))

    def test_list_uses_only_idx_as_document_id(self):
        url = "https://itc.pknu.ac.kr/html/06/01.php?category_idx=36"
        request_url, params = self.adapter.list_request(url, 3)
        self.assertEqual("https://itc.pknu.ac.kr/html/06/01.php", request_url)
        self.assertEqual({"category_idx": "36", "pagenum": 3}, params)
        items = self.adapter.parse_list(BeautifulSoup(self.board, "lxml"), url)
        self.assertEqual(["524", "523"], [x["source_id"] for x in items])
        self.assertNotEqual("36", items[0]["source_id"])

    def test_detail_body_date_and_attachment(self):
        url = "https://itc.pknu.ac.kr/html/06/01.php?mode=read&idx=524&category_idx=36"
        parsed = self.adapter.parse_detail(BeautifulSoup(self.detail, "lxml"), url,
                                           {"source_id": "524", "is_notice": True},
                                           base_url=BASE, site_prefix="itc")
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual("524", parsed["source_id"])
        self.assertEqual("2026-09-02", parsed["date"])
        self.assertEqual("상세 본문입니다.", parsed["body"])
        self.assertEqual("https://itc.pknu.ac.kr/upload/file_download.php?file_idx=88",
                         parsed["attachments"][0]["url"])


if __name__ == "__main__": unittest.main()

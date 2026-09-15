from pathlib import Path
import unittest

from bs4 import BeautifulSoup

from scripts.crawlers.departments.adapters import get_adapter
from scripts.crawlers.departments.config import load_registry
from scripts.crawlers.departments.discovery import discover_from_html
from scripts.crawlers.departments.probe import analyze_site_html
from scripts.crawlers.departments.redirects import follow_meta_refresh_once, meta_refresh_target


FIXTURES = Path(__file__).parent / "fixtures" / "departments" / "geoinfo"
BASE = "http://geoinfo.pknu.ac.kr/"


class FakeResponse:
    def __init__(self, url: str, text: str):
        self.url, self.text, self.status_code = url, text, 200
        self.encoding, self.apparent_encoding = "utf-8", "utf-8"

    def raise_for_status(self) -> None:
        return None


class FakeSession:
    def __init__(self, response: FakeResponse):
        self.response, self.calls = response, []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


class GeoinfoLegacyPHPAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.adapter = get_adapter("legacy_php")
        cls.root = (FIXTURES / "root_meta.html").read_text(encoding="utf-8")
        cls.home = (FIXTURES / "homepage.html").read_text(encoding="utf-8")
        cls.static = (FIXTURES / "static.html").read_text(encoding="utf-8")
        cls.board = (FIXTURES / "board.html").read_text(encoding="utf-8")
        cls.detail = (FIXTURES / "detail.html").read_text(encoding="utf-8")

    def test_meta_refresh_is_same_host_http_and_followed_once(self) -> None:
        self.assertEqual(BASE + "00main/main.php", meta_refresh_target(self.root, BASE))
        loop = (FIXTURES / "loop_meta.html").read_text(encoding="utf-8")
        session = FakeSession(FakeResponse(BASE + "00main/main.php", loop))
        response, followed = follow_meta_refresh_once(session, FakeResponse(BASE, self.root), timeout=1)
        self.assertTrue(followed)
        self.assertEqual(1, len(session.calls))
        self.assertEqual(BASE + "00main/main.php", response.url)

    def test_external_and_unsafe_meta_refresh_are_blocked(self) -> None:
        with self.assertRaisesRegex(ValueError, "external"):
            meta_refresh_target('<meta http-equiv="refresh" content="0;url=https://outside.example/main.php">', BASE)
        with self.assertRaisesRegex(ValueError, "unsafe"):
            meta_refresh_target('<meta http-equiv="refresh" content="0;url=javascript:alert(1)">', BASE)

    def test_probe_and_discovery_distinguish_static_and_board(self) -> None:
        probe = analyze_site_html(site_key="fixture", requested_url=BASE, html=self.home,
                                  final_url=BASE + "00main/main.php")
        self.assertTrue(probe.compatible)
        menus = self.adapter.discover_menus(self.home, BASE + "00main/main.php")
        self.assertEqual(3, len(menus))
        html = {m["url"]: self.board if "/05piazza/" in m["url"] else self.static for m in menus}
        result = discover_from_html(site_key="fixture", base_url=BASE, homepage_html=self.home,
                                    section_html=html, adapter_name="legacy_php")
        sections = {section.path: section for section in result.sections}
        self.assertEqual("static_page", sections["/01about/00.php"].kind)
        self.assertEqual("board", sections["/05piazza/08.php"].kind)
        self.assertEqual("cate0501", sections["/05piazza/08.php"].bbs_id)

    def test_list_pagination_detail_and_attachment(self) -> None:
        board_url = BASE + "05piazza/08.php"
        request_url, params = self.adapter.list_request(board_url, 2, "cate0501")
        self.assertEqual(board_url, request_url)
        self.assertEqual({"p": 2, "bbscode": "cate0501"}, params)
        items = self.adapter.parse_list(BeautifulSoup(self.board, "lxml"), board_url)
        self.assertEqual(["8800", "8799"], [item["source_id"] for item in items])
        self.assertEqual([True, False], [item["is_notice"] for item in items])
        parsed = self.adapter.parse_detail(BeautifulSoup(self.detail, "lxml"), items[0]["post_url"], items[0],
                                           base_url=BASE, site_prefix="geoinfo")
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual("8800", parsed["source_id"])
        self.assertEqual("학과사무실", parsed["author"])
        self.assertEqual("2026-08-31", parsed["date"])
        self.assertEqual("상세 본문입니다.", parsed["body"])
        self.assertEqual("http://geoinfo.pknu.ac.kr/program/amibbs/bbsExe.php?idx=4627&bbscode=cate0501&mode=down",
                         parsed["attachments"][0]["url"])

    def test_registry_contains_only_reviewed_sections(self) -> None:
        config = load_registry()["geoinfo"]
        self.assertTrue(config.enabled)
        self.assertEqual("legacy_php", config.adapter)
        self.assertEqual(16, len(config.sections))
        self.assertEqual(7, sum(section.kind == "static_page" for section in config.sections))
        self.assertEqual(9, sum(section.kind == "board" for section in config.sections))
        self.assertTrue(all(section.bbs_id for section in config.sections if section.kind == "board"))


if __name__ == "__main__":
    unittest.main()

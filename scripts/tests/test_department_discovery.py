from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.crawlers.departments.cli import save_json_atomic
from scripts.crawlers.departments.config import load_registry, load_site_catalog, validate_catalog
from scripts.crawlers.departments.discovery import (
    analyze_section_html, discover_from_html, extract_bbs_id,
)
from scripts.crawlers.departments.probe import analyze_site_html, numeric_menu_links


WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_ROOT = Path(__file__).with_name("fixtures") / "departments" / "numeric_cms"


class DepartmentDiscoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.homepage = (FIXTURE_ROOT / "homepage.html").read_text(encoding="utf-8")
        cls.board = (FIXTURE_ROOT / "list.html").read_text(encoding="utf-8")
        cls.static = (FIXTURE_ROOT / "static.html").read_text(encoding="utf-8")

    def test_catalog_validation_reports_known_quality_issues(self) -> None:
        report = validate_catalog(load_site_catalog(), load_registry())
        self.assertEqual("valid", report["status"])
        self.assertEqual(100, report["stats"]["rows"])
        self.assertEqual(99, report["stats"]["homepages"])
        self.assertEqual(98, report["stats"]["unique_homepages"])
        self.assertEqual(1, report["stats"]["missing_homepages"])
        self.assertEqual(0, report["stats"]["duplicate_homepages"])
        self.assertEqual([], report["errors"])

    def test_probe_recognizes_department_cms_fingerprint(self) -> None:
        result = analyze_site_html(
            site_key="ce.pknu.ac.kr", requested_url="https://ce.pknu.ac.kr/",
            final_url="https://ce.pknu.ac.kr/", html=self.homepage,
        )
        self.assertEqual("compatible", result.status)
        self.assertTrue(result.compatible)
        self.assertEqual(3, result.fingerprints["numeric_menu_links"])
        self.assertGreaterEqual(result.confidence, 0.6)

    def test_probe_rejects_unrelated_html(self) -> None:
        result = analyze_site_html(
            site_key="example.test", requested_url="https://example.test",
            html="<html><body>plain page</body></html>",
        )
        self.assertEqual("unsupported", result.status)
        self.assertFalse(result.compatible)

    def test_numeric_menu_discovery_excludes_external_links(self) -> None:
        menus = numeric_menu_links(self.homepage, "https://ce.pknu.ac.kr/")
        self.assertEqual(3, len(menus))
        self.assertTrue(all(menu["url"].startswith("https://ce.pknu.ac.kr/") for menu in menus))

    def test_board_and_bbs_id_are_discovered(self) -> None:
        url = "https://ce.pknu.ac.kr/ce/1814"
        self.assertEqual("2400536", extract_bbs_id(self.board, url))
        section = analyze_section_html(name="학부공지", page_url=url, html=self.board)
        self.assertEqual("board", section.kind)
        self.assertEqual("2400536", section.bbs_id)
        self.assertEqual("notice", section.document_type)
        self.assertEqual("candidate", section.status)

    def test_faculty_profile_view_links_are_static_pages(self) -> None:
        html = (
            Path(__file__).with_name("fixtures") / "departments" / "faculty_page.html"
        ).read_text(encoding="utf-8")
        for name, url in (
            ("교수진소개", "https://kukmun.pknu.ac.kr/korean/5178"),
            ("보직교수", "https://cns.pknu.ac.kr/cns/226"),
            ("교수진 소개", "https://microbiology.pknu.ac.kr/microbiology/288"),
            ("교수진", "https://ice.pknu.ac.kr/pknuice/840"),
            ("교수 소개", "https://masscom.pknu.ac.kr/comm/3123"),
        ):
            with self.subTest(name=name):
                section = analyze_section_html(name=name, page_url=url, html=html)
                self.assertEqual("static_page", section.kind)
                self.assertEqual("candidate", section.status)
                self.assertIsNone(section.bbs_id)

    def test_discovery_returns_board_static_and_unsupported_candidates(self) -> None:
        result = discover_from_html(
            site_key="ce.pknu.ac.kr", base_url="https://ce.pknu.ac.kr/",
            homepage_html=self.homepage,
            section_html={
                "https://ce.pknu.ac.kr/ce/1814": self.board,
                "https://ce.pknu.ac.kr/ce/1803": self.static,
            },
        )
        self.assertEqual("success", result.status)
        self.assertEqual("ce", result.site_prefix)
        self.assertEqual(["board", "static_page", "static_page"], [s.kind for s in result.sections])
        self.assertEqual(["candidate", "candidate", "unsupported"], [s.status for s in result.sections])

    def test_discovery_result_is_saved_atomically(self) -> None:
        with tempfile.TemporaryDirectory(dir=WORKSPACE_ROOT) as directory:
            path = Path(directory) / "result.json"
            save_json_atomic(path, {"status": "success", "sections": []})
            self.assertEqual("success", json.loads(path.read_text(encoding="utf-8"))["status"])
            self.assertFalse(list(path.parent.glob("*.tmp")))


if __name__ == "__main__":
    unittest.main()

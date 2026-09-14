from __future__ import annotations

import unittest
from pathlib import Path

from bs4 import BeautifulSoup

from scripts.crawlers.departments.discovery import discover_from_html
from scripts.crawlers.departments.engine import extract_body_content
from scripts.crawlers.departments.config import load_registry
from scripts.crawlers.departments.probe import analyze_site_html, select_content_container


FIXTURE_ROOT = Path(__file__).with_name("fixtures") / "departments" / "dmfbe"
BASE_URL = "https://dmfbe.pknu.ac.kr/DMFBE"


class DmfbeAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.homepage = (FIXTURE_ROOT / "homepage.html").read_text(encoding="utf-8")
        cls.hub_page = (FIXTURE_ROOT / "hub_page.html").read_text(encoding="utf-8")

    def test_probe_recognizes_container_wrap_layout(self) -> None:
        result = analyze_site_html(
            site_key="dmfbe.pknu.ac.kr",
            requested_url="https://dmfbe.pknu.ac.kr/",
            final_url=BASE_URL,
            html=self.homepage,
        )
        self.assertEqual("compatible", result.status)
        self.assertTrue(result.fingerprints["content_container"])
        self.assertEqual(4, result.fingerprints["numeric_menu_links"])

    def test_discovery_rejects_navigation_and_external_redirects(self) -> None:
        section_html = {
            f"{BASE_URL}/1": self.hub_page,
            f"{BASE_URL}/9999": self.hub_page,
            f"{BASE_URL}/8077": self.hub_page,
            f"{BASE_URL}/8078": self.hub_page,
        }
        result = discover_from_html(
            site_key="dmfbe.pknu.ac.kr",
            base_url=BASE_URL,
            homepage_html=self.homepage,
            section_html=section_html,
            section_final_urls={
                f"{BASE_URL}/8077": "https://mbe.pknu.ac.kr/mbe",
                f"{BASE_URL}/8078": "https://ree.pknu.ac.kr/ree/1",
            },
        )
        self.assertEqual("no_candidates", result.status)
        self.assertTrue(all(section.status == "unsupported" for section in result.sections))
        self.assertEqual("navigation-only menu", result.sections[0].warnings[0])
        self.assertEqual("navigation-only menu", result.sections[1].warnings[0])
        self.assertTrue(result.sections[2].warnings[0].startswith("external redirect:"))
        self.assertTrue(result.sections[3].warnings[0].startswith("external redirect:"))

    def test_common_parser_extracts_hash_container_content(self) -> None:
        soup = BeautifulSoup(self.hub_page, "lxml")
        container = select_content_container(soup)
        self.assertIsNotNone(container)
        content = extract_body_content(container)
        self.assertIn("해양수산경영학전공", content)
        self.assertIn("자원환경경제학전공", content)

    def test_hub_stays_disabled_while_redirect_targets_are_registered(self) -> None:
        registry = load_registry()
        self.assertFalse(registry["dmfbe"].enabled)
        self.assertEqual((), registry["dmfbe"].sections)
        self.assertIn("mbe", registry)
        self.assertIn("ree", registry)
        self.assertEqual("mbe.pknu.ac.kr", registry["mbe"].source_catalog_key)
        self.assertEqual("ree.pknu.ac.kr", registry["ree"].source_catalog_key)
        self.assertNotEqual(registry["dmfbe"].base_url, registry["mbe"].base_url)
        self.assertNotEqual(registry["dmfbe"].base_url, registry["ree"].base_url)


if __name__ == "__main__":
    unittest.main()

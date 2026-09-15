from __future__ import annotations

import unittest
from pathlib import Path

from bs4 import BeautifulSoup

from scripts.crawlers.departments.adapters import DepartmentCMSAdapter, get_adapter
from scripts.crawlers.departments.config import DepartmentConfig, load_registry
from scripts.crawlers.departments.discovery import analyze_section_html


class DepartmentAdapterTests(unittest.TestCase):
    def test_default_config_uses_numeric_adapter(self) -> None:
        config = DepartmentConfig.from_dict({
            "dataset": "sample", "name": "Sample", "base_url": "https://sample.test",
            "site_prefix": "sample", "enabled": False, "sections": [],
        })
        self.assertEqual("numeric_cms", config.adapter)
        self.assertIsInstance(get_adapter(config.adapter), DepartmentCMSAdapter)

    def test_discovery_compatibility_wrapper_accepts_adapter_name(self) -> None:
        section = analyze_section_html(
            name="소개", page_url="https://sample.test/sample/2",
            html='<main><p>content</p></main>', adapter_name="numeric_cms",
        )
        self.assertEqual("static_page", section.kind)
        self.assertEqual("candidate", section.status)

    def test_unknown_adapter_fails_before_parsing(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsupported department CMS adapter"):
            analyze_section_html(
                name="소개", page_url="https://sample.test/sample/2",
                html="<main />", adapter_name="missing",
            )

    def test_fishsci_and_ice_first_board_sections_match_adapter_fixtures(self) -> None:
        registry = load_registry()
        fixture_root = Path(__file__).with_name("fixtures") / "departments"
        cases = (
            ("fishsci", "menu_44", "query_view_do", fixture_root / "fishsci" / "board.html"),
            ("ice", "menu_895", "numeric_cms", fixture_root / "numeric_cms" / "list.html"),
        )
        for dataset, section_id, adapter_name, fixture in cases:
            with self.subTest(dataset=dataset):
                config = registry[dataset]
                first_board = next(section for section in config.active_sections if section.kind == "board")
                self.assertEqual(section_id, first_board.id)
                html = fixture.read_text(encoding="utf-8")
                items = get_adapter(adapter_name).parse_list(
                    BeautifulSoup(html, "lxml"), first_board.runtime_dict(config.base_url)["url"],
                )
                self.assertGreater(len(items), 0)

        fishsci_sections = {section.id: section for section in registry["fishsci"].active_sections}
        self.assertEqual("static_page", fishsci_sections["menu_36"].kind)
        self.assertEqual("guide", fishsci_sections["menu_36"].document_type)


if __name__ == "__main__":
    unittest.main()

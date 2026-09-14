from __future__ import annotations

import unittest

from scripts.crawlers.departments.adapters import DepartmentCMSAdapter, get_adapter
from scripts.crawlers.departments.config import DepartmentConfig
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


if __name__ == "__main__":
    unittest.main()

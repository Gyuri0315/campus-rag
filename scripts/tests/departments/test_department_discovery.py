from __future__ import annotations

import unittest
from dataclasses import replace
from unittest.mock import patch

from scripts.crawlers.departments.adapters import get_adapter
from scripts.crawlers.departments.config import DepartmentConfig
from scripts.crawlers.departments.discovery import analyze_section_html, discover_from_html, extract_bbs_id
from scripts.crawlers.departments.freshness import add_result_provenance, assess_result_freshness, probe_discovery_warnings
from scripts.crawlers.departments.probe import analyze_site_html, numeric_menu_links
from scripts.crawlers.departments.registry_ops import plan_discovery_updates
from scripts.tests._paths import FIXTURES_ROOT


FIXTURE_ROOT = FIXTURES_ROOT / "departments" / "numeric_cms"


class DepartmentDiscoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.homepage = (FIXTURE_ROOT / "homepage.html").read_text(encoding="utf-8")
        cls.board = (FIXTURE_ROOT / "list.html").read_text(encoding="utf-8")
        cls.static = (FIXTURE_ROOT / "static.html").read_text(encoding="utf-8")


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


def config() -> DepartmentConfig:
    return DepartmentConfig.from_dict({
        "dataset": "econ", "name": "경제학과", "base_url": "https://econ.pknu.ac.kr",
        "site_prefix": "econ", "source_catalog_key": "econ.pknu.ac.kr",
        "enabled": False, "sections": [],
    })


class DepartmentFreshnessTests(unittest.TestCase):
    def test_provenance_records_adapter_hash_url_and_time(self) -> None:
        payload = add_result_provenance({"status": "success"}, config())
        provenance = payload["provenance"]
        self.assertEqual({"name": "numeric_cms", "version": "1.0"}, provenance["adapter"])
        self.assertEqual(64, len(provenance["registry_config_hash"]))
        self.assertEqual("https://econ.pknu.ac.kr", provenance["registry_url"])
        self.assertIn("+09:00", provenance["executed_at"])
        self.assertEqual("fresh", assess_result_freshness(payload, config())["status"])

    def test_adapter_url_and_version_changes_are_stale(self) -> None:
        original = add_result_provenance({"status": "success"}, config())
        changed_url = replace(config(), base_url="https://new.example.test")
        self.assertIn("url_changed", assess_result_freshness(original, changed_url)["reasons"])
        changed_adapter = replace(config(), adapter="query_view_do")
        self.assertIn("adapter_changed", assess_result_freshness(original, changed_adapter)["reasons"])
        adapter = get_adapter("numeric_cms")
        with patch.object(adapter, "version", "2.0"):
            self.assertIn("adapter_version_changed", assess_result_freshness(original, config())["reasons"])

    def test_legacy_result_is_readable_but_not_fresh(self) -> None:
        result = assess_result_freshness({"status": "success", "sections": []}, config())
        self.assertEqual("legacy", result["status"])
        self.assertFalse(result["fresh"])

    def test_blocked_probe_is_distinct_from_old_no_candidates(self) -> None:
        warnings = probe_discovery_warnings(
            {"status": "blocked"}, {"status": "no_candidates"},
        )
        self.assertEqual("BLOCKED_PROBE_DISCOVERY_CONFLICT", warnings[0]["code"])
        self.assertIn("no_candidates", warnings[0]["message"])

    def test_apply_discovery_skips_legacy_result(self) -> None:
        payload = {"schema_version": "1.0", "departments": [{
            "dataset": "econ", "name": "경제학과", "base_url": "https://econ.pknu.ac.kr",
            "site_prefix": "econ", "source_catalog_key": "econ.pknu.ac.kr",
            "enabled": False, "sections": [],
        }]}
        root = FIXTURES_ROOT / "departments" / "freshness"
        updated, report = plan_discovery_updates(payload, [config()], root)
        self.assertEqual(payload, updated)
        self.assertEqual("legacy_discovery", report["skipped"][0]["reason"])


if __name__ == "__main__":
    unittest.main()

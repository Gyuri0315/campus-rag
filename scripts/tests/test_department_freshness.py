from __future__ import annotations

import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from scripts.crawlers.departments.adapters import get_adapter
from scripts.crawlers.departments.config import DepartmentConfig
from scripts.crawlers.departments.freshness import (
    add_result_provenance, assess_result_freshness, probe_discovery_warnings,
)
from scripts.crawlers.departments.registry_ops import plan_discovery_updates


ROOT = Path(__file__).resolve().parents[2]


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
        root = ROOT / "scripts" / "tests" / "fixtures" / "departments" / "freshness"
        updated, report = plan_discovery_updates(payload, [config()], root)
        self.assertEqual(payload, updated)
        self.assertEqual("legacy_discovery", report["skipped"][0]["reason"])


if __name__ == "__main__":
    unittest.main()

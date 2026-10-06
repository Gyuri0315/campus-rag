from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.crawlers.departments import engine
from scripts.crawlers.departments.config import DEFAULT_REGISTRY_PATH, DepartmentConfig, SectionConfig, audit_registry, load_registry
from scripts.crawlers.departments.engine import crawl_ready_configs
from scripts.migrations.department_registry_categories import plan_registry
from scripts.tests._paths import FIXTURES_ROOT


class DepartmentConfigTests(unittest.TestCase):


    def test_all_selects_only_crawl_ready_datasets(self) -> None:
        registry = load_registry()
        ready = crawl_ready_configs(registry)
        self.assertEqual(
            [config.dataset for config in registry.values() if config.enabled and config.active_sections],
            [config.dataset for config in ready],
        )

    def test_all_runs_every_ready_dataset_without_network(self) -> None:
        expected = [config.dataset for config in crawl_ready_configs(load_registry())]
        argv = ["engine.py", "--all", "--once"]
        with patch.object(sys, "argv", argv), patch.object(engine, "_run_config", return_value=0) as run:
            self.assertEqual(0, engine.main())
        self.assertEqual(expected, [call.args[0].dataset for call in run.call_args_list])


    def test_damaged_or_path_unsafe_category_is_rejected(self) -> None:
        for category in ("????", "미분류", "공지/자료"):
            with self.subTest(category=category), self.assertRaisesRegex(ValueError, "category"):
                SectionConfig.from_dict({
                    "id": "notice", "name": "공지", "category": category,
                    "kind": "board", "path": "/sample/100", "bbs_id": "123",
                    "document_type": "notice",
                })

    def test_dataset_section_id_and_path_are_execution_safe(self) -> None:
        base = {
            "dataset": "sample", "name": "Sample", "base_url": "https://sample.test",
            "site_prefix": "sample", "enabled": True,
            "sections": [{
                "id": "notice", "name": "공지", "category": "공지사항",
                "kind": "board", "path": "/sample/100", "bbs_id": "123",
                "document_type": "notice",
            }],
        }
        cases = (
            ("reserved dataset", {"dataset": "con"}),
            ("unsafe section id", {"section_id": "notice/one"}),
            ("protocol-relative path", {"path": "//evil.example/path"}),
            ("path fragment", {"path": "/sample/100#fragment"}),
            ("control character", {"path": "/sample/\n100"}),
            ("unknown adapter", {"adapter": "unknown"}),
            ("unknown document type", {"document_type": "article"}),
        )
        for label, change in cases:
            payload = json.loads(json.dumps(base))
            if "dataset" in change:
                payload["dataset"] = change["dataset"]
            if "adapter" in change:
                payload["adapter"] = change["adapter"]
            if "section_id" in change:
                payload["sections"][0]["id"] = change["section_id"]
            if "path" in change:
                payload["sections"][0]["path"] = change["path"]
            if "document_type" in change:
                payload["sections"][0]["document_type"] = change["document_type"]
            with self.subTest(case=label), self.assertRaises(ValueError):
                DepartmentConfig.from_dict(payload)

    def test_registry_audit_reports_every_invalid_dataset(self) -> None:
        invalid = (
            FIXTURES_ROOT / "departments"
            / "invalid_registry_datasets.json"
        )
        audit = audit_registry(invalid)
        self.assertEqual(2, audit.registered)
        self.assertEqual({}, audit.configs)
        self.assertEqual({"bad-category", "con"}, {error["dataset"] for error in audit.errors})


    def test_category_repair_changes_no_non_category_fields(self) -> None:
        payload = {
            "departments": [{
                "dataset": "sample", "enabled": True,
                "sections": [{
                    "id": "notice", "name": "공지사항", "category": "????",
                    "kind": "board", "path": "/sample/1", "bbs_id": "123",
                    "document_type": "notice", "enabled": True,
                }],
            }],
        }
        before = dict(payload["departments"][0]["sections"][0])
        plan = plan_registry(payload)
        after = payload["departments"][0]["sections"][0]
        self.assertEqual([], plan["unresolved"])
        self.assertEqual("공지사항", after["category"])
        self.assertEqual(
            {key: value for key, value in before.items() if key != "category"},
            {key: value for key, value in after.items() if key != "category"},
        )


    def test_section_builds_absolute_runtime_url(self) -> None:
        section = SectionConfig.from_dict({
            "id": "notice", "name": "공지", "category": "공지사항",
            "kind": "board", "path": "/sample/100", "bbs_id": "123",
            "document_type": "notice",
        })
        runtime = section.runtime_dict("https://sample.pknu.ac.kr")
        self.assertEqual("https://sample.pknu.ac.kr/sample/100", runtime["url"])
        self.assertTrue(runtime["is_board"])

    def test_invalid_board_without_bbs_id_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "requires bbs_id"):
            DepartmentConfig.from_dict({
                "dataset": "sample", "name": "Sample", "base_url": "https://sample.test",
                "site_prefix": "sample", "enabled": False,
                "sections": [{"id": "notice", "name": "공지", "category": "공지사항",
                "kind": "board", "path": "/sample/100", "document_type": "notice"}],
            })

    def test_query_view_do_board_does_not_require_numeric_bbs_id(self) -> None:
        config = DepartmentConfig.from_dict({
            "dataset": "sample", "name": "Sample", "base_url": "https://sample.test",
            "site_prefix": "kor", "adapter": "query_view_do", "enabled": False,
            "sections": [{"id": "menu_44", "name": "공지", "category": "공지사항",
                          "kind": "board", "path": "/kor/view.do?no=44",
                          "document_type": "notice"}],
        })
        self.assertIsNone(config.sections[0].bbs_id)

    def test_duplicate_dataset_is_rejected(self) -> None:
        payload = json.loads(DEFAULT_REGISTRY_PATH.read_text(encoding="utf-8"))
        payload["departments"].append(dict(payload["departments"][0]))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "registry.json"
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "duplicate datasets"):
                load_registry(path)


if __name__ == "__main__":
    unittest.main()

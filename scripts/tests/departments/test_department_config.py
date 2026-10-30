from __future__ import annotations

import json
import sys
import tempfile
import unittest
import copy
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from scripts.crawlers.departments import engine
from scripts.crawlers.departments.config import DEFAULT_REGISTRY_PATH, DepartmentConfig, SectionConfig, audit_registry, load_registry
from scripts.crawlers.departments.engine import crawl_ready_configs
from scripts.crawlers.departments.parallel import build_plan, exclusive_lock
from scripts.migrations.department_registry_categories import plan_registry
from scripts.tests._paths import FIXTURES_ROOT


class DepartmentConfigTests(unittest.TestCase):

    def test_parallel_plan_refuses_a_live_sequential_batch(self) -> None:
        with self.assertRaisesRegex(ValueError, "Stop the existing"):
            build_plan({"status": "running"}, {}, 4)

    def test_parallel_plan_preserves_pending_targets_and_shared_redirect_hosts(self) -> None:
        base = next(config for config in load_registry().values() if config.crawl_ready)
        registry = {name: replace(base, dataset=name, base_url=f"https://{name}.example.test",
                                  tls_legacy_server_hosts=()) for name in ["finished", "paused", "shared", "newsite"]}
        source = {"status": "cancelled", "expected_datasets": ["finished", "paused", "shared"],
                  "results": [{"dataset": "finished", "status": "partial_success"},
                              {"dataset": "paused", "status": "cancelled"}]}
        evidence = {"results": [{"dataset": name, "hops": [{"host": "cms.example.test"}]}
                                for name in ["paused", "shared"]]}
        plan = build_plan(source, registry, 2, evidence)
        assigned = [name for group in plan["groups"] for name in group["datasets"]]
        self.assertCountEqual(["paused", "shared", "newsite"], assigned)
        self.assertEqual(len(assigned), len(set(assigned)))
        self.assertTrue(any({"paused", "shared"}.issubset(group["datasets"]) for group in plan["groups"]))
        self.assertEqual([{"dataset": "finished", "status": "partial_success"}], plan["previous_results"])

    def test_parallel_duplicate_claim_is_rejected_and_released(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with exclusive_lock(Path(tmp), "dataset:sample"):
                with self.assertRaises(OSError):
                    with exclusive_lock(Path(tmp), "dataset:sample"):
                        self.fail("Duplicate worker acquired the same dataset")
            with exclusive_lock(Path(tmp), "dataset:sample"):
                pass


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
        with patch.object(sys, "argv", argv), patch.object(engine, "save_json_atomic"), patch.object(engine, "_run_config", return_value=0) as run:
            self.assertEqual(0, engine.main())
        self.assertEqual(expected, [call.args[0].dataset for call in run.call_args_list])

    def test_batch_keeps_later_targets_and_report_after_dataset_exception(self) -> None:
        expected = [config.dataset for config in crawl_ready_configs(load_registry())]
        snapshots = []
        def capture(_path, report):
            snapshots.append(copy.deepcopy(report))
        calls = 0
        def run(_config, _args):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("unexpected parser error")
            return 0
        with patch.object(sys, "argv", ["engine.py", "--all", "--once"]), \
             patch.object(engine, "save_json_atomic", side_effect=capture), \
             patch.object(engine, "_run_config", side_effect=run), \
             patch("sys.stdout"):
            self.assertEqual(1, engine.main())
        final = snapshots[-1]
        self.assertEqual(expected, final["expected_datasets"])
        self.assertEqual(expected, [row["dataset"] for row in final["results"]])
        self.assertEqual("failed", final["results"][0]["status"])
        self.assertEqual("needs_attention", final["status"])
        self.assertEqual("running", snapshots[1]["status"])

    def test_batch_cancellation_keeps_remaining_targets_visible(self) -> None:
        expected = [config.dataset for config in crawl_ready_configs(load_registry())]
        snapshots = []
        with patch.object(sys, "argv", ["engine.py", "--all", "--once"]), \
             patch.object(engine, "save_json_atomic", side_effect=lambda _p, r: snapshots.append(copy.deepcopy(r))), \
             patch.object(engine, "_run_config", side_effect=KeyboardInterrupt) as run, \
             patch("sys.stdout"):
            self.assertEqual(130, engine.main())
        run.assert_called_once()
        final = snapshots[-1]
        self.assertEqual(expected, final["expected_datasets"])
        self.assertEqual("cancelled", final["status"])
        self.assertEqual(130, final["results"][0]["exit_code"])


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
            ("storage traversal", {"storage_path": ["..", "sample"]}),
            ("wrong storage leaf", {"storage_path": ["College", "other"]}),
            ("storage string", {"storage_path": "College/sample"}),
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
            if "storage_path" in change:
                payload["storage_path"] = change["storage_path"]
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

from __future__ import annotations

import io
import json
import sys
import unittest
from unittest.mock import patch

from scripts.crawlers.departments import cli, engine
from scripts.crawlers.departments.config import DepartmentConfig, load_registry
from scripts.crawlers.departments.engine import crawl_ready_configs
from scripts.crawlers.departments.preflight import build_preflight, classify_preflight
from scripts.tests._paths import PROJECT_ROOT, FIXTURES_ROOT


ROOT = PROJECT_ROOT
DISCOVERY_ROOT = ROOT / "files" / "_discovery"


class DepartmentPreflightTests(unittest.TestCase):
    def test_ready_matches_all_crawl_targets(self) -> None:
        registry = load_registry()
        report = build_preflight(registry, discovery_root=DISCOVERY_ROOT)
        ready = [item["dataset"] for item in report["items"] if item["status"] == "ready"]
        self.assertEqual([config.dataset for config in crawl_ready_configs(registry)], ready)
        self.assertEqual(len(registry), report["summary"]["registered"])


    def test_unverified_adapter_result_is_requires_adapter(self) -> None:
        config = DepartmentConfig(
            dataset="mpsm", name="fixture", base_url="https://mpsm.example.test",
            site_prefix="mpsm", sections=(), enabled=False,
        )
        discovery = {
            "status": "no_candidates",
            "sections": [{"status": "requires_adapter", "warnings": ["adapter required"]}],
        }
        with patch(
            "scripts.crawlers.departments.preflight._load_result",
            side_effect=lambda _root, _config, name: discovery if name == "discovery.json" else {},
        ):
            item = classify_preflight(config, discovery_root=DISCOVERY_ROOT)
        self.assertEqual("requires_adapter", item["status"])

    def test_list_ready_table_and_json_are_read_only(self) -> None:
        for json_output in (False, True):
            argv = ["cli.py", "list-ready"] + (["--json"] if json_output else [])
            with self.subTest(json=json_output), patch.object(sys, "argv", argv), patch(
                "scripts.crawlers.departments.cli.probe_site",
                side_effect=AssertionError("network probe must not run"),
            ), patch("sys.stdout", new_callable=io.StringIO) as output:
                self.assertEqual(0, cli.main())
                text = output.getvalue()
                if json_output:
                    payload = json.loads(text)
                    self.assertIn("ready", payload["summary"])
                else:
                    self.assertIn("STATUS", text)
                    self.assertIn("humanict", text)

    def test_invalid_registry_returns_one(self) -> None:
        invalid = FIXTURES_ROOT / "departments" / "invalid_registry.json"
        argv = ["cli.py", "--registry", str(invalid), "list-ready"]
        with patch.object(sys, "argv", argv), patch("sys.stderr", new_callable=io.StringIO):
            self.assertEqual(1, cli.main())

    def test_invalid_datasets_are_reported_and_never_reach_network_or_crawl(self) -> None:
        invalid = (
            FIXTURES_ROOT / "departments"
            / "invalid_registry_datasets.json"
        )
        argv = ["cli.py", "--registry", str(invalid), "list-ready", "--json"]
        with patch.object(sys, "argv", argv), patch(
            "scripts.crawlers.departments.cli.probe_site",
            side_effect=AssertionError("network probe must not run"),
        ), patch("sys.stdout", new_callable=io.StringIO) as output:
            self.assertEqual(1, cli.main())
            report = json.loads(output.getvalue())
        invalid_items = {item["dataset"]: item for item in report["items"]}
        self.assertEqual("invalid", invalid_items["bad-category"]["status"])
        self.assertIn("invalid category", invalid_items["bad-category"]["validation_errors"][0]["message"])
        self.assertEqual("invalid", invalid_items["con"]["status"])
        self.assertEqual(0, report["summary"]["ready"])
        self.assertEqual(2, report["summary"]["invalid"])

        argv = ["engine.py", "--registry", str(invalid), "--all", "--once"]
        with patch.object(sys, "argv", argv), patch.object(
            engine, "_run_config", side_effect=AssertionError("crawl must not run"),
        ), patch("sys.stderr", new_callable=io.StringIO) as errors:
            self.assertEqual(1, engine.main())
        self.assertIn("INVALID_DATASET_CONFIG", errors.getvalue())

    def test_invalid_cli_usage_returns_two(self) -> None:
        with patch.object(sys, "argv", ["cli.py", "list-ready", "--unknown"]):
            with self.assertRaises(SystemExit) as raised:
                cli.main()
        self.assertEqual(2, raised.exception.code)


if __name__ == "__main__":
    unittest.main()

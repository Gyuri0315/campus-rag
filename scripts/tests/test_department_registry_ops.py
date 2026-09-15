from __future__ import annotations

import argparse
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.crawlers.common.logging import configure_crawler_logging
from scripts.crawlers.departments import cli
from scripts.crawlers.departments.config import DepartmentConfig, DepartmentSite, load_registry
from scripts.crawlers.departments.discovery import DiscoveryResult
from scripts.crawlers.departments.probe import ProbeResult
from scripts.crawlers.departments.registry_ops import accepted_sections, plan_discovery_updates
from scripts.crawlers.departments.freshness import add_result_provenance


WORKSPACE_ROOT = Path(__file__).resolve().parents[2]


def sample_config() -> DepartmentConfig:
    return DepartmentConfig.from_dict({
        "dataset": "econ", "name": "경제학과",
        "base_url": "https://econ.pknu.ac.kr", "site_prefix": "econ",
        "source_catalog_key": "econ.pknu.ac.kr", "enabled": False,
        "sections": [],
    })


class DepartmentRegistryOpsTests(unittest.TestCase):
    def test_board_without_bbs_id_is_not_accepted(self) -> None:
        sections, warnings = accepted_sections({"sections": [{
            "id": "notice", "name": "공지", "category": "미분류",
            "kind": "board", "path": "/econ/1", "document_type": "notice",
            "bbs_id": None, "status": "candidate",
        }]})
        self.assertEqual([], sections)
        self.assertIn("skipped board without bbs_id: notice", warnings)

    def test_query_view_do_board_without_bbs_id_is_accepted(self) -> None:
        sections, warnings = accepted_sections({"sections": [{
            "id": "menu_44", "name": "공지사항", "category": "커뮤니티",
            "kind": "board", "path": "/kor/view.do?no=44",
            "document_type": "notice", "bbs_id": None, "status": "candidate",
        }]}, adapter_name="query_view_do")
        self.assertEqual("menu_44", sections[0]["id"])
        self.assertEqual([], warnings)

    def test_plan_is_non_mutating_and_enables_usable_discovery(self) -> None:
        payload = {"schema_version": "1.0", "departments": [{
            "dataset": "econ", "name": "경제학과",
            "base_url": "https://econ.pknu.ac.kr", "site_prefix": "econ",
            "source_catalog_key": "econ.pknu.ac.kr", "enabled": False,
            "sections": [],
        }]}
        discovery = {
            "status": "success", "site_prefix": "econ", "sections": [{
                "id": "menu_1", "name": "학과소개", "category": "학과안내",
                "kind": "static_page", "path": "/econ/1",
                "document_type": "guide", "bbs_id": None,
                "status": "candidate", "confidence": 0.8,
            }],
        }
        discovery = add_result_provenance(discovery, sample_config())
        with tempfile.TemporaryDirectory(dir=WORKSPACE_ROOT) as directory:
            path = Path(directory) / "econ.pknu.ac.kr" / "discovery.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(discovery, ensure_ascii=False), encoding="utf-8")
            updated, report = plan_discovery_updates(payload, [sample_config()], Path(directory))
        self.assertFalse(payload["departments"][0]["enabled"])
        self.assertTrue(updated["departments"][0]["enabled"])
        self.assertEqual(1, len(updated["departments"][0]["sections"]))
        self.assertTrue(report["dry_run"])
        self.assertEqual(1, report["summary"]["changed"])

    def test_batch_emits_live_progress_events(self) -> None:
        console = io.StringIO()
        loggers = []
        site = DepartmentSite(
            college="인문사회과학대학", department="경제학과", major=None,
            homepage="https://econ.pknu.ac.kr", compatibility=None, notes=None,
            dataset="econ",
        )
        probe = ProbeResult(
            site_key="econ.pknu.ac.kr", requested_url=site.homepage,
            final_url=site.homepage, status="compatible", compatible=True,
            confidence=1.0, http_status=200,
        )
        discovery = DiscoveryResult(
            site_key="econ.pknu.ac.kr", base_url=site.homepage,
            status="success", site_prefix="econ", sections=[], errors=[],
        )
        with tempfile.TemporaryDirectory(dir=WORKSPACE_ROOT) as directory:
            root = Path(directory)
            args = argparse.Namespace(
                registry=root / "registry.json", sites=root / "sites.csv",
                command="discover-all", timeout=1, output_root=root / "discovery",
                max_sections=1, request_delay=0.0, force=True,
            )

            def setup_logger(*_args, **_kwargs):
                logger, context = configure_crawler_logging(
                    "department_discovery", root, console_stream=console,
                )
                loggers.append(logger)
                return logger, context

            with (
                patch.object(cli, "load_registry", return_value={"econ": sample_config()}),
                patch.object(cli, "load_site_catalog", return_value=[site]),
                patch.object(cli, "probe_site", return_value=probe),
                patch.object(cli, "discover_site", return_value=discovery),
                patch.object(cli, "configure_crawler_logging", side_effect=setup_logger),
            ):
                self.assertEqual(0, cli.run_batch(args))
            for logger in loggers:
                for handler in list(logger.handlers):
                    handler.close()
                    logger.removeHandler(handler)
            self.assertTrue((root / "discovery" / "discover-all.json").exists())

        output = console.getvalue()
        for event in (
            "batch_started", "probe_started", "probe_finished",
            "discovery_started", "discovery_finished", "batch_progress", "batch_finished",
        ):
            self.assertIn(event, output)
        self.assertIn("completed=1", output)
        self.assertIn("percent=100.0", output)


if __name__ == "__main__":
    unittest.main()

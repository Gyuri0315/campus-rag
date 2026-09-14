from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.crawlers.departments.config import (
    DEFAULT_REGISTRY_PATH, DepartmentConfig, SectionConfig, get_department, load_registry,
    load_site_catalog, validate_catalog, validate_registry_catalog_links,
)
from scripts.crawlers.departments import engine
from scripts.crawlers.departments.engine import configure_department, crawl_ready_configs


WORKSPACE_ROOT = Path(__file__).resolve().parents[2]


class DepartmentConfigTests(unittest.TestCase):
    def test_absolute_section_path_replaces_base_path(self) -> None:
        runtime = SectionConfig(
            id="notice", name="공지사항", category="공지사항", kind="board",
            path="/future/5285", document_type="notice", bbs_id="2402553",
        ).runtime_dict("https://future.pknu.ac.kr/future/1")
        self.assertEqual("https://future.pknu.ac.kr/future/5285", runtime["url"])

    def test_ce_configuration_is_loaded_from_registry(self) -> None:
        registry = load_registry()
        self.assertEqual(98, len(registry))
        self.assertIn("ce", registry)
        self.assertIn("econ", registry)
        ce = registry["ce"]
        self.assertEqual("https://ce.pknu.ac.kr", ce.base_url)
        self.assertEqual("ce", ce.site_prefix)
        self.assertEqual(17, len(ce.sections))
        self.assertEqual(5, sum(section.kind == "board" for section in ce.sections))
        self.assertEqual(12, sum(section.kind == "static_page" for section in ce.sections))
        self.assertEqual("ce.pknu.ac.kr", ce.source_catalog_key)
        self.assertEqual("numeric_cms", ce.adapter)
        self.assertTrue(ce.crawl_ready)
        self.assertTrue(registry["econ"].crawl_ready)
        self.assertEqual("numeric_cms", registry["econ"].adapter)
        self.assertEqual("경제학과", registry["econ"].department)

    def test_all_selects_only_crawl_ready_datasets(self) -> None:
        registry = load_registry()
        ready = crawl_ready_configs(registry)
        self.assertEqual(96, len(ready))
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

    def test_registry_has_no_unclassified_or_duplicate_sections(self) -> None:
        for config in load_registry().values():
            with self.subTest(dataset=config.dataset):
                self.assertTrue(all(section.category != "미분류" for section in config.sections))
                self.assertEqual(len(config.sections), len({section.id for section in config.sections}))
                self.assertEqual(len(config.sections), len({section.path for section in config.sections}))
                board_ids = [s.bbs_id for s in config.sections if s.kind == "board" and s.bbs_id]
                self.assertEqual(len(board_ids), len(set(board_ids)))

    def test_curated_partial_discoveries_are_crawl_ready(self) -> None:
        registry = load_registry()
        expected = {
            "kukmun": (15, "/korean/5178"),
            "cns": (5, "/cns/226"),
            "microbiology": (9, "/microbiology/288"),
            "ice": (17, "/pknuice/840"),
        }
        excluded_paths = {
            "/korean/5201", "/cns/249", "/microbiology/517",
            "/pknuice/7483", "/pknuice/7741",
        }
        for dataset, (section_count, faculty_path) in expected.items():
            with self.subTest(dataset=dataset):
                config = registry[dataset]
                self.assertTrue(config.crawl_ready)
                self.assertEqual(section_count, len(config.sections))
                faculty = next(section for section in config.sections if section.path == faculty_path)
                self.assertEqual("static_page", faculty.kind)
                self.assertIsNone(faculty.bbs_id)
                self.assertTrue(excluded_paths.isdisjoint(section.path for section in config.sections))

    def test_masscom_sections_are_curated_and_crawl_ready(self) -> None:
        masscom = load_registry()["masscom"]
        self.assertTrue(masscom.crawl_ready)
        self.assertEqual("comm", masscom.site_prefix)
        self.assertEqual(13, len(masscom.sections))
        faculty = next(section for section in masscom.sections if section.id == "menu_3123")
        self.assertEqual("static_page", faculty.kind)
        self.assertEqual("전공소개", faculty.category)
        self.assertEqual("guide", faculty.document_type)
        self.assertIsNone(faculty.bbs_id)
        excluded = {"menu_1", "menu_9999", "menu_3117", "menu_3118", "menu_3119"}
        self.assertTrue(excluded.isdisjoint(section.id for section in masscom.sections))

    def test_source_csv_is_loaded_without_losing_rows(self) -> None:
        sites = load_site_catalog()
        self.assertEqual(100, len(sites))
        self.assertEqual(99, sum(site.homepage is not None for site in sites))
        self.assertEqual(98, len({site.site_key for site in sites if site.site_key}))
        ce = [site for site in sites if site.site_key == "ce.pknu.ac.kr"]
        self.assertEqual(1, len(ce))
        self.assertEqual("컴퓨터·인공지능공학부", ce[0].department)
        self.assertEqual("컴퓨터공학전공, 인공지능전공", ce[0].major)
        self.assertEqual("ce", ce[0].dataset)

        econ = [site for site in sites if site.site_key == "econ.pknu.ac.kr"]
        self.assertEqual(1, len(econ))
        self.assertEqual("econ", econ[0].dataset)

    def test_catalog_dataset_aliases_are_reported(self) -> None:
        report = validate_catalog(load_site_catalog(), load_registry())
        self.assertEqual(99, report["stats"]["datasets"])
        self.assertEqual(98, report["stats"]["unique_datasets"])
        self.assertEqual(0, report["stats"]["duplicate_datasets"])
        duplicate_aliases = {
            warning["dataset"] for warning in report["warnings"]
            if warning["code"] == "DUPLICATE_DATASET_ALIAS"
        }
        self.assertEqual(set(), duplicate_aliases)

        datasets_by_major = {
            site.major: site.dataset for site in load_site_catalog() if site.major
        }
        self.assertEqual("ps1", datasets_by_major["사회복지학전공"])
        self.assertEqual("bigdata", datasets_by_major["빅데이터융합전공"])
        self.assertEqual("welfare", datasets_by_major["사회복지서비스학전공"])

    def test_registry_links_to_source_catalog(self) -> None:
        validate_registry_catalog_links(load_registry(), load_site_catalog())

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
        with tempfile.TemporaryDirectory(dir=WORKSPACE_ROOT) as directory:
            path = Path(directory) / "registry.json"
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "duplicate datasets"):
                load_registry(path)

    def test_unknown_cms_adapter_is_rejected(self) -> None:
        payload = json.loads(DEFAULT_REGISTRY_PATH.read_text(encoding="utf-8"))
        payload["departments"][0]["adapter"] = "unknown"
        with tempfile.TemporaryDirectory(dir=WORKSPACE_ROOT) as directory:
            path = Path(directory) / "registry.json"
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "unsupported department CMS adapter"):
                load_registry(path)

    def test_engine_uses_dataset_specific_paths_and_sections(self) -> None:
        config = get_department("ce")
        configure_department(config)
        from scripts.crawlers.departments import engine

        self.assertEqual("ce", engine.PATHS.dataset)
        self.assertEqual("https://ce.pknu.ac.kr/ce/1814", engine.SECTIONS[0]["url"])
        self.assertEqual("2400536", engine.SECTIONS[0]["bbs_id"])


if __name__ == "__main__":
    unittest.main()

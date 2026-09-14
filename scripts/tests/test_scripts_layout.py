from __future__ import annotations

import unittest
from pathlib import Path

from scripts.crawlers.common import schema
from scripts.crawlers import pknu_notice, pknu_rule, pknu_student_life
from scripts.crawlers.departments import engine as department_engine
from scripts.migrations import crawler_documents, crawler_storage


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class ScriptsLayoutTests(unittest.TestCase):
    def test_common_contract_exports_are_canonical(self) -> None:
        self.assertEqual("scripts.crawlers.common.schema", schema.CrawlStats.__module__)
        self.assertEqual("scripts.crawlers.common.schema", schema.RunResult.__module__)

    def test_canonical_entry_points_export_main(self) -> None:
        for module in (department_engine, pknu_notice, pknu_rule, pknu_student_life):
            with self.subTest(module=module.__name__):
                self.assertTrue(callable(module.main))
        for module in (crawler_documents, crawler_storage):
            with self.subTest(module=module.__name__):
                self.assertTrue(callable(module.main))

    def test_internal_pipeline_uses_canonical_crawler_paths(self) -> None:
        pipeline_files = (
            PROJECT_ROOT / "scripts" / "rag" / "pipelining.py",
            PROJECT_ROOT / "scripts" / "rag" / "preprocessing_pipeline_recent.py",
        )
        legacy_paths = (
            "scripts/ce/crawler.py", "scripts/main/notice_crawler.py",
            "scripts/main/student_life_crawler.py", "scripts/rule/crawler.py",
        )
        for path in pipeline_files:
            content = path.read_text(encoding="utf-8")
            for legacy_path in legacy_paths:
                self.assertNotIn(legacy_path, content)

    def test_legacy_compatibility_files_are_removed(self) -> None:
        legacy_files = (
            "scripts/crawler_schema.py", "scripts/crawler_reader.py",
            "scripts/crawler_storage.py", "scripts/crawler_logging.py",
            "scripts/ce/crawler.py", "scripts/main/notice_crawler.py",
            "scripts/main/student_life_crawler.py", "scripts/rule/crawler.py",
            "scripts/migrate_crawler_documents.py", "scripts/migrate_crawler_storage.py",
            "scripts/crawlers/ce.py",
        )
        for relative_path in legacy_files:
            with self.subTest(path=relative_path):
                self.assertFalse((PROJECT_ROOT / relative_path).exists())


if __name__ == "__main__":
    unittest.main()

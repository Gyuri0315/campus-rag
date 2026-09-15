from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.crawlers.common.schema import CrawlStats, RunResult, classify_document


WORKSPACE_ROOT = Path(__file__).resolve().parents[2]


class CrawlerRunResultTests(unittest.TestCase):
    def test_success_and_integer_defaults(self) -> None:
        result = RunResult("ce", "incremental", stats=CrawlStats(discovered=2, requested=2, new=2)).finish()
        self.assertEqual("success", result.status)
        self.assertEqual(0, result.exit_code)
        self.assertTrue(all(type(value) is int for value in result.to_dict()["stats"].values()))

    def test_partial_success_for_document_failure(self) -> None:
        result = RunResult("ce", "incremental", stats=CrawlStats(requested=2, new=1, failed=1))
        result.add_error("DETAIL_FAILED", "failed", source_id="10", url="https://example/10", retryable=True)
        result.finish()
        self.assertEqual("partial_success", result.status)
        self.assertEqual(0, result.exit_code)

    def test_initialization_failure_has_exit_code_one(self) -> None:
        result = RunResult("rule", "full")
        result.add_error("RUN_INITIALIZATION_FAILED", "state unavailable")
        result.finish("failed")
        self.assertEqual(1, result.exit_code)

    def test_unchanged_and_mixed_classification(self) -> None:
        self.assertEqual("new", classify_document("", "a"))
        self.assertEqual("unchanged", classify_document("a", "a"))
        self.assertEqual("updated", classify_document("a", "b"))

    def test_attachment_failure_makes_partial_success(self) -> None:
        stats = CrawlStats(requested=1, unchanged=1)
        stats.count_attachments([
            {"downloaded": True, "error": None},
            {"downloaded": False, "error": {"code": "HTTP_500"}},
        ])
        result = RunResult("pknu_notice", "incremental", stats=stats).finish()
        self.assertEqual((2, 1, 1), (
            stats.attachments_discovered, stats.attachments_downloaded, stats.attachments_failed,
        ))
        self.assertEqual("partial_success", result.status)

    def test_stats_add_preserves_totals(self) -> None:
        total = CrawlStats(new=1, skipped=2).add(CrawlStats(updated=3, failed=1))
        self.assertEqual((1, 3, 2, 1), (total.new, total.updated, total.skipped, total.failed))

    def test_result_path_and_json_schema(self) -> None:
        with tempfile.TemporaryDirectory(dir=WORKSPACE_ROOT) as directory:
            root = Path(directory)
            result = RunResult("pknu_student_life", "smoke", stats=CrawlStats(skipped=3)).finish()
            path = result.save(root)
            self.assertEqual(root / "files" / "pknu_student_life" / "output" / "runs" / f"{result.run_id}.json", path)
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual("1.0", payload["schema_version"])
            self.assertIn("+09:00", payload["started_at"])
            self.assertIn("+09:00", payload["finished_at"])
            self.assertIsInstance(payload["elapsed_seconds"], float)


if __name__ == "__main__":
    unittest.main()

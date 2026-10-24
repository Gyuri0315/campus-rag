from __future__ import annotations

import sys
import unittest
from unittest.mock import patch

from scripts.crawlers.common.schema import CrawlStats
from scripts.crawlers.departments import engine
from scripts.crawlers.departments.config import load_registry


class DepartmentSmokeCliTests(unittest.TestCase):
    def test_smoke_options_are_forwarded_without_network(self) -> None:
        section_id = load_registry()["ce"].active_sections[0].id
        argv = [
            "engine.py", "--dataset", "ce", "--section", section_id,
            "--max-items", "2", "--no-download-files", "--once",
        ]
        with patch.object(sys, "argv", argv), patch.object(engine, "_run_config", return_value=0) as run:
            self.assertEqual(0, engine.main())
        args = run.call_args.args[1]
        self.assertEqual(section_id, args.section)
        self.assertEqual(2, args.max_items)
        self.assertTrue(args.no_download_files)

    def test_non_positive_max_items_is_cli_error(self) -> None:
        with patch.object(sys, "argv", ["engine.py", "--dataset", "ce", "--max-items", "0"]):
            with self.assertRaises(SystemExit) as raised:
                engine.main()
        self.assertEqual(2, raised.exception.code)

    def test_non_positive_max_board_sections_is_cli_error(self) -> None:
        with patch.object(sys, "argv", ["engine.py", "--all", "--max-board-sections", "0"]):
            with self.assertRaises(SystemExit) as raised:
                engine.main()
        self.assertEqual(2, raised.exception.code)

    def test_section_and_max_board_sections_are_mutually_exclusive(self) -> None:
        argv = ["engine.py", "--dataset", "ce", "--section", "menu_175", "--max-board-sections", "1"]
        with patch.object(sys, "argv", argv):
            with self.assertRaises(SystemExit) as raised:
                engine.main()
        self.assertEqual(2, raised.exception.code)

    def test_unknown_section_is_cli_error(self) -> None:
        argv = ["engine.py", "--dataset", "ce", "--section", "does-not-exist"]
        with patch.object(sys, "argv", argv):
            with self.assertRaises(SystemExit) as raised:
                engine.main()
        self.assertEqual(2, raised.exception.code)

    def test_section_cannot_be_combined_with_all(self) -> None:
        argv = ["engine.py", "--all", "--section", "notice"]
        with patch.object(sys, "argv", argv):
            with self.assertRaises(SystemExit) as raised:
                engine.main()
        self.assertEqual(2, raised.exception.code)

    def test_section_filter_and_max_items_apply_across_sections(self) -> None:
        sections = [
            {"id": "one", "name": "One", "url": "https://example.test/1", "is_board": False},
            {"id": "two", "name": "Two", "url": "https://example.test/2", "is_board": False},
        ]
        with (
            patch.object(engine, "SECTIONS", sections),
            patch.object(engine, "load_state", return_value={"items": {}}),
            patch.object(engine, "save_state"),
            patch.object(engine, "build_session", return_value=object()),
            patch.object(engine, "crawl_static", return_value=CrawlStats(discovered=1, requested=1, new=1)) as crawl,
        ):
            stats = engine.run_crawl(max_items=1)
        self.assertEqual(1, crawl.call_count)
        self.assertEqual(1, stats.requested)

    def test_max_board_sections_skips_static_pages_and_preserves_order(self) -> None:
        sections = [
            {"id": "static", "name": "Static", "url": "https://example.test/s", "is_board": False},
            {"id": "one", "name": "One", "url": "https://example.test/1", "is_board": True},
            {"id": "two", "name": "Two", "url": "https://example.test/2", "is_board": True},
        ]
        with (
            patch.object(engine, "SECTIONS", sections),
            patch.object(engine, "load_state", return_value={"items": {}}),
            patch.object(engine, "save_state"),
            patch.object(engine, "build_session", return_value=object()),
            patch.object(engine, "crawl_board", return_value=(CrawlStats(requested=1), 0)) as board,
            patch.object(engine, "crawl_static") as static,
        ):
            engine.run_crawl(max_board_sections=1, recent_only=1, max_items=1, no_download_files=True)
        self.assertEqual(1, board.call_count)
        self.assertEqual("one", board.call_args.args[1]["id"])
        self.assertEqual(1, board.call_args.kwargs["recent_only"])
        self.assertEqual(1, board.call_args.kwargs["max_items"])
        self.assertTrue(board.call_args.kwargs["no_download_files"])
        static.assert_not_called()

    def test_no_download_metadata_uses_common_attachment_schema(self) -> None:
        attachments = engine.attachment_metadata_only(
            [{"name": "guide.pdf", "url": "https://example.test/guide.pdf"}],
            "https://example.test/view",
        )
        self.assertEqual(1, len(attachments))
        self.assertFalse(attachments[0]["downloaded"])
        self.assertIsNone(attachments[0]["saved_path"])
        self.assertEqual("not_attempted", attachments[0]["text"]["status"])


if __name__ == "__main__":
    unittest.main()

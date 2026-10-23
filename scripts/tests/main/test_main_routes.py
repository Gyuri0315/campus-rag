from __future__ import annotations

import unittest
from contextlib import nullcontext
from unittest.mock import patch

from scripts.main import run as main_run
from scripts.main import routes


class MainRouteTests(unittest.TestCase):
    def test_invalid_route_configuration_is_rejected_before_crawling(self) -> None:
        with patch.dict(routes.ROUTE_GROUPS, {"static": (*routes.STATIC_PAGE_IDS, 100)}):
            with self.assertRaisesRegex(ValueError, "excluded main pages"):
                routes._build_routes()
        with patch.object(routes, "NO_FOLLOW_LINK_PAGE_IDS", frozenset({999})):
            with self.assertRaisesRegex(ValueError, "no-follow pages must be static"):
                routes._build_routes()

    def test_runner_delegates_only_registered_pages(self) -> None:
        def collected(items, **options):
            self.assertTrue(all(item["category_path"][0] == options["category"]
                                for item in items))
            for item in items:
                item["status"] = "completed"
            return {"status": "completed"}

        with (patch.object(main_run, "_write_json") as write_json,
              patch("scripts.main.categories.student_life.execute", side_effect=collected) as student_life,
              patch("scripts.main.categories.community.execute", side_effect=collected) as community,
              patch("scripts.main.categories.university_intro.execute", side_effect=collected) as intro):
            report = main_run.execute([95, 163, 434, 38, 39, 999], year=2026,
                                      months=list(range(1, 13)), full_resync=False,
                                      board_pages=1, report_path=main_run.DEFAULT_REPORT)

        student_life.assert_called_once()
        community.assert_called_once()
        intro.assert_called_once()
        self.assertEqual([item["page_id"] for item in student_life.call_args.args[0]], [95, 434])
        self.assertEqual([item["page_id"] for item in community.call_args.args[0]], [163])
        self.assertEqual([item["page_id"] for item in intro.call_args.args[0]], [38, 39])
        statuses = {row["page_id"]: row["status"] for row in report["routes"]}
        self.assertEqual(statuses, {95: "completed", 163: "completed", 434: "completed",
                                    38: "completed", 39: "completed", 999: "needs_review"})
        self.assertEqual(report["status"], "needs_review")
        write_json.assert_called_once()

    def test_curriculum_route_keeps_ocr_preview_out_of_console_report(self) -> None:
        result = {
            "status": "needs_review", "output": "files/pknu_main/대학생활/교육과정/json/pages/main_106.json",
            "attachment_count": 1,
            "attachments": [{"title": "안내서", "text_preview": "« OCR 결과", "text_status": "success"}],
        }
        with (patch.object(main_run, "_write_json"),
              patch("scripts.main.categories.student_life.build_session", return_value=nullcontext(object())),
              patch("scripts.main.collectors.curriculum_files.collect_curriculum_files",
                    return_value=result)):
            report = main_run.execute([106], year=2026, months=list(range(1, 13)),
                                      full_resync=False, board_pages=1,
                                      report_path=main_run.DEFAULT_REPORT)
        route = report["routes"][0]
        self.assertEqual(route["status"], "needs_review")
        self.assertNotIn("text_preview", route["attachments"][0])


if __name__ == "__main__":
    unittest.main()

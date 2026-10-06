from __future__ import annotations

import json
import unittest
from contextlib import nullcontext
from unittest.mock import patch

from scripts.crawlers import pknu_student_life
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
        class Stats:
            failed = 0

            def to_dict(self):
                return {"failed": 0}

        class RouteReport:
            def read_text(self, *, encoding):
                return json.dumps({"routes": [
                    {"page_id": 95, "status": "success", "route": "board"}
                ]})

            def relative_to(self, root):
                return main_run.DEFAULT_REPORT.relative_to(root)

        with (patch.object(main_run, "STUDENT_LIFE_ROUTE_REPORT", RouteReport()),
              patch.object(main_run, "_write_json") as write_json,
              patch("scripts.crawlers.pknu_student_life.run", return_value=Stats()) as student_life,
              patch("scripts.crawlers.pknu_notice.run_crawl", return_value=Stats()) as notice):
            report = main_run.execute([95, 163, 434, 999], year=2026,
                                      months=list(range(1, 13)), full_resync=False,
                                      board_pages=1, report_path=main_run.DEFAULT_REPORT)

        self.assertEqual(student_life.call_count, 2)
        self.assertEqual(student_life.call_args_list[0].kwargs["page_ids"], (95,))
        self.assertEqual(student_life.call_args_list[1].args[0], "guide")
        notice.assert_called_once()
        statuses = {row["page_id"]: row["status"] for row in report["routes"]}
        self.assertEqual(statuses, {95: "success", 163: "completed",
                                    434: "completed", 999: "needs_review"})
        self.assertEqual(report["status"], "needs_review")
        write_json.assert_called_once()

    def test_curriculum_route_keeps_ocr_preview_out_of_console_report(self) -> None:
        result = {
            "status": "needs_review", "output": "files/pknu_main/대학생활/교육과정/json/pages/main_106.json",
            "attachment_count": 1,
            "attachments": [{"title": "안내서", "text_preview": "« OCR 결과", "text_status": "success"}],
        }
        with (patch.object(main_run, "_write_json"),
              patch.object(pknu_student_life, "build_session", return_value=nullcontext(object())),
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

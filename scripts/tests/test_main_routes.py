from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from scripts.main import run as main_run
from scripts.main.routes import ROUTES, STUDENT_LIFE_PAGE_IDS, plan
from scripts.crawlers import pknu_student_life


class MainRouteTests(unittest.TestCase):
    def test_registry_has_one_verified_handler_per_page(self) -> None:
        self.assertEqual(ROUTES[31].handler, "academic_calendar")
        for page_id in (92, 94, 230, 231, 232, 242, 243, 244, 245, 246):
            self.assertEqual(ROUTES[page_id].handler, "static")
            self.assertIn(page_id, STUDENT_LIFE_PAGE_IDS)
        for page_id in (233, 234, 235):
            self.assertEqual(ROUTES[page_id].handler, "major_program")
            self.assertNotIn(page_id, STUDENT_LIFE_PAGE_IDS)
        self.assertEqual(ROUTES[102].handler, "tuition")
        self.assertEqual(ROUTES[163].handler, "notice")
        self.assertEqual(ROUTES[434].handler, "guide")
        self.assertEqual(ROUTES[533].handler, "organization")
        self.assertNotIn(31, STUDENT_LIFE_PAGE_IDS)
        self.assertEqual(pknu_student_life.CONFIGURED_PAGE_IDS, STUDENT_LIFE_PAGE_IDS)
        self.assertEqual(plan([31, 999, 31])[-1]["status"], "needs_review")

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


if __name__ == "__main__":
    unittest.main()

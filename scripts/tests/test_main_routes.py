from __future__ import annotations

import json
import unittest
from contextlib import nullcontext
from unittest.mock import patch

from scripts.main import run as main_run
from scripts.main.routes import ROUTES, STUDENT_LIFE_PAGE_IDS, NO_FOLLOW_LINK_PAGE_IDS, plan
from scripts.crawlers import pknu_student_life


class MainRouteTests(unittest.TestCase):
    def test_registry_has_one_verified_handler_per_page(self) -> None:
        self.assertEqual(ROUTES[31].handler, "academic_calendar")
        self.assertNotIn(100, ROUTES)
        self.assertEqual(ROUTES[104].handler, "static")
        self.assertEqual(pknu_student_life.static_page_subcategory(114), "학생생활")
        self.assertEqual(pknu_student_life.static_page_subcategory(115), "학생생활")
        self.assertEqual(pknu_student_life.static_page_subcategory(449), "학생생활")
        for page_id in (257, 258):
            self.assertEqual(ROUTES[page_id].handler, "static")
            self.assertEqual(pknu_student_life.static_page_subcategory(page_id), "학생생활")
        for page_id in (118, 259, 260):
            self.assertEqual(ROUTES[page_id].handler, "static")
            self.assertEqual(pknu_student_life.static_page_subcategory(page_id), "학생생활")
        for page_id in (438, 494):
            self.assertEqual(ROUTES[page_id].handler, "static")
            self.assertEqual(pknu_student_life.static_page_subcategory(page_id), "학생생활")
        self.assertIn(494, NO_FOLLOW_LINK_PAGE_IDS)
        for page_id in (263, 264):
            self.assertEqual(ROUTES[page_id].handler, "static")
            self.assertEqual(pknu_student_life.static_page_subcategory(page_id), "학생생활")
        self.assertIn(263, NO_FOLLOW_LINK_PAGE_IDS)
        self.assertEqual(pknu_student_life.static_page_subcategory(262), "학생생활")
        self.assertIn(262, NO_FOLLOW_LINK_PAGE_IDS)
        self.assertEqual(pknu_student_life.static_page_subcategory(101), "학사정보")
        self.assertEqual(pknu_student_life.static_page_subcategory(104), "학사정보")
        self.assertEqual(pknu_student_life.static_page_subcategory(247), "학사정보")
        self.assertEqual(pknu_student_life.static_page_subcategory(248), "학사안내_페이지")
        self.assertEqual(pknu_student_life.static_page_subcategory(999), "학생생활")
        for page_id in (306, 307, 308):
            self.assertEqual(ROUTES[page_id].handler, "static")
            self.assertEqual(pknu_student_life.static_page_subcategory(page_id), "학사정보")
            self.assertIn(page_id, NO_FOLLOW_LINK_PAGE_IDS)
        for page_id in (92, 94, 230, 231, 232, 242, 243, 244, 245, 246):
            self.assertEqual(ROUTES[page_id].handler, "static")
            self.assertIn(page_id, STUDENT_LIFE_PAGE_IDS)
        for page_id in (233, 234, 235):
            self.assertEqual(ROUTES[page_id].handler, "major_program")
            self.assertNotIn(page_id, STUDENT_LIFE_PAGE_IDS)
        self.assertEqual(ROUTES[102].handler, "tuition")
        self.assertEqual(ROUTES[399].handler, "board")
        self.assertIn(399, STUDENT_LIFE_PAGE_IDS)
        self.assertEqual(pknu_student_life.board_post_subcategory(399),
                         "교내_식당_주간식단표")
        self.assertEqual(pknu_student_life.board_post_subcategory(95), "학점교류_게시판")
        self.assertEqual(ROUTES[106].handler, "curriculum_files")
        self.assertEqual(ROUTES[362].handler, "curriculum_files")
        self.assertNotIn(472, ROUTES)
        self.assertEqual(plan([472])[0]["status"], "excluded")
        self.assertNotIn(416, ROUTES)
        self.assertNotIn(416, STUDENT_LIFE_PAGE_IDS)
        self.assertEqual(plan([416])[0]["status"], "excluded")
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

    def test_curriculum_route_keeps_ocr_preview_out_of_console_report(self) -> None:
        result = {
            "status": "needs_review", "output": "files/pknu_student_life/output/교육과정/main_106.json",
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

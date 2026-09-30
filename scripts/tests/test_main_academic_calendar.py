from __future__ import annotations

import json
import unittest

from scripts.main.collectors.academic_calendar import _site_id, parse_month


class AcademicCalendarTests(unittest.TestCase):
    def test_reads_only_the_schedule_widget_source(self) -> None:
        html = '''<div id="subCont"><tbody id="scheduleList"></tbody></div>
        <script>function getMonthList() { $.ajax({url : "/getScheduleList.do",
        data : {steId : 'IEav5ISH'}}); }</script>'''
        self.assertEqual(_site_id(html), "IEav5ISH")
        with self.assertRaisesRegex(ValueError, "table is missing"):
            _site_id(html.replace('id="scheduleList"', 'id="other"'))

    def test_bad_source_date_is_reviewable_without_losing_valid_rows(self) -> None:
        rows = [
            {"schSeq": 1, "schNm": "개강", "schCont": "개강",
             "strDt": "20260901", "endDt": "20260901", "steId": "IEav5ISH"},
            {"schSeq": 2, "schNm": "잘못된 일정", "strDt": "20261221",
             "endDt": "20260112", "steId": "IEav5ISH"},
        ]
        events, rejected = parse_month(json.dumps({"list": rows}).encode(),
                                       year=2026, month=9, site_id="IEav5ISH")
        self.assertEqual([event["source_record_id"] for event in events], [1])
        self.assertEqual(rejected[0]["reason"], "end date precedes start date")
        self.assertEqual(rejected[0]["raw_end_date"], "20260112")

    def test_unexpected_site_is_not_accepted(self) -> None:
        payload = json.dumps({"list": [{"steId": "other"}]}).encode()
        with self.assertRaisesRegex(ValueError, "unexpected site ID"):
            parse_month(payload, year=2026, month=9, site_id="IEav5ISH")


if __name__ == "__main__":
    unittest.main()

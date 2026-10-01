from __future__ import annotations

import unittest
from unittest.mock import patch

from scripts.crawlers.pknu_student_life import parse_guide_items_from_html


class MainGuideTests(unittest.TestCase):
    def test_contest_pdfs_are_discovered_with_section_context(self) -> None:
        html = """<div id="subCont"><div class="content_wrap">
          <h4 class="subNameH4">2024학년도 「대학생활계획서 콘테스트」 우수작</h4>
          <ul><li>우수작 1번(PDF) 보기</li><div class="uploadPdf" data-id="6260"></div>
          <li>우수작 2번(PDF) 보기</li><div class="uploadPdf" data-id="6262"></div></ul>
          </div></div>"""
        with patch(
            "scripts.crawlers.pknu_student_life.resolve_media_pdf_url",
            side_effect=lambda _session, media_id: f"https://www.pknu.ac.kr/upload/{media_id}.pdf",
        ):
            items = parse_guide_items_from_html(None, html)
        self.assertEqual([item.media_id for item in items], ["6260", "6262"])
        self.assertEqual([item.title for item in items], [
            "2024학년도 대학생활계획서 콘테스트 우수작 1번",
            "2024학년도 대학생활계획서 콘테스트 우수작 2번",
        ])
        self.assertEqual([item.year for item in items], [2024, 2024])
        self.assertEqual([item.subcategory for item in items], ["학생생활", "학생생활"])


if __name__ == "__main__":
    unittest.main()

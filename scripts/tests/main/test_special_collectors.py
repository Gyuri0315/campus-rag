from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from scripts.crawlers import pknu_student_life as crawler
from scripts.crawlers.common.schema import attachment_text, validate_common_document
from scripts.crawlers.pknu_student_life import parse_guide_items_from_html
from scripts.main.collectors.academic_calendar import _site_id, parse_month
from scripts.main.collectors.curriculum_files import _clean_page_text, _is_visually_blank, parse_curriculum_attachments
from scripts.main.collectors.major_program import parse_major_program_page


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


class MajorProgramTests(unittest.TestCase):
    def test_image_inside_layout_table_is_collected(self) -> None:
        html = """<div id="subCont"><h3 class="subTitle">마이크로전공</h3>
          <div class="content_wrap"><table><tr><td>
          <img src="/upload/guide.jpg" alt="안내문"></td></tr></table></div></div>"""
        title, images = parse_major_program_page(html, 235)
        self.assertEqual(title, "마이크로전공")
        self.assertEqual(images, [{"order": 1, "src": "/upload/guide.jpg", "alt": "안내문"}])

    def test_new_html_body_text_requires_parser_review(self) -> None:
        html = """<div id="subCont"><h3 class="subTitle">전공제도</h3>
          <div class="content_wrap"><p>새 안내</p><img src="/guide.jpg"></div></div>"""
        with self.assertRaisesRegex(ValueError, "HTML body text"):
            parse_major_program_page(html, 233)


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


class CurriculumFileTests(unittest.TestCase):
    def test_collects_pdf_attachments_without_ebook_links(self) -> None:
        html = """
        <div id="subCont"><h3 class="subTitle">교육과정 안내</h3>
          <div class="content_wrap">
            <a href="/ebook/curr/index.html">ebook 바로가기</a>
            <a download="2026 교육과정 안내서" href="/upload/media/guide.pdf">PDF 다운로드</a>
            <a href="https://portal.pknu.ac.kr/">포털 바로가기</a>
          </div>
        </div>
        """
        title, attachments = parse_curriculum_attachments(html, 106)
        self.assertEqual(title, "교육과정 안내")
        self.assertEqual(attachments, [{
            "title": "2026 교육과정 안내서",
            "filename": "2026 교육과정 안내서.pdf",
            "url": "https://www.pknu.ac.kr/upload/media/guide.pdf",
        }])

    def test_rejects_an_unverified_attachment_host(self) -> None:
        html = """
        <div id="subCont"><h3 class="subTitle">비교과 교육과정 안내</h3>
          <div class="content_wrap">
            <a download="자료" href="https://example.org/guide.pdf">PDF</a>
          </div>
        </div>
        """
        with self.assertRaisesRegex(ValueError, "unexpected curriculum attachment URL"):
            parse_curriculum_attachments(html, 362)

    def test_page_text_cleanup_preserves_content_and_removes_ocr_noise(self) -> None:
        raw = "21\n|  비교과   교육과정  |\n___\n내용\u00a0 설명\n내용  설명\n21"
        self.assertEqual(
            _clean_page_text(raw, 21, ocr=True),
            "비교과 교육과정\n내용 설명",
        )

    def test_blank_pdf_page_is_not_sent_to_ocr(self) -> None:
        import fitz

        with fitz.open() as pdf:
            page = pdf.new_page()
            self.assertTrue(_is_visually_blank(page))
            page.insert_text((72, 72), "Visible text")
            self.assertFalse(_is_visually_blank(page))


class TuitionFaqTests(unittest.TestCase):
    def test_attachment_only_answer_is_extracted_without_following_payment_tab(self) -> None:
        faq_url = "https://www.pknu.ac.kr/main/250"
        post_url = f"{faq_url}?action=view&no=999001"
        list_html = """
            <div id="subCont"><a href="/main/251">등록금납부</a>
            <input name="bbsId" value="86"><span>1</span> / 1
            <table class="brdList"><tbody><tr>
              <td class="bdlNum">1</td><td class="bdlDate">2026-09-01</td>
              <td><a href="?action=view&no=999001">질문</a></td>
            </tr></tbody></table></div>
        """
        detail_html = """
            <form id="frmPost"><input name="bbsId" value="86">
              <input name="chkNo" value="999001"></form>
            <div class="bdCont"><table><tr class="first_noti">
              <td class="title_b">등록금 질문</td></tr></table>
              <div class="bdvTxt"></div>
              <a href="/boardDownload.do?no=42">answer.pdf</a></div>
        """
        requested: list[str] = []

        def fake_fetch(_session: object, url: str) -> SimpleNamespace:
            requested.append(url)
            if url == faq_url:
                html = list_html
            elif url == post_url:
                html = detail_html
            else:
                raise AssertionError(f"unexpected request: {url}")
            return SimpleNamespace(status_code=200, url=url,
                                   headers={"Content-Type": "text/html"}, text=html)

        attachment = {
            "id": "attachment-001", "name": "answer.pdf",
            "url": "https://www.pknu.ac.kr/boardDownload.do?no=42",
            "final_url": None, "saved_path": "files/answer.pdf", "downloaded": True,
            "content_type": "application/pdf", "size_bytes": 10, "sha256": "a" * 64,
            "text": attachment_text(), "error": None,
        }
        with (patch.object(crawler, "fetch", side_effect=fake_fetch),
              patch.object(crawler.pknu_notice, "save_attachments", return_value=[attachment]),
              patch.object(crawler, "extract_pdf_text", return_value="PDF의 답변")):
            faq, docs = crawler.collect_tuition_faq(object(), faq_url)

        self.assertEqual(requested, [faq_url, post_url])
        self.assertEqual(faq["item_count"], 1)
        self.assertIn("PDF의 답변", faq["items"][0]["content"])
        self.assertEqual(docs[0]["subcategory"], "학사정보")
        self.assertEqual(docs[0]["metadata"]["parent_page_id"], 102)
        self.assertEqual(docs[0]["attachments"][0]["text"]["status"], "success")
        self.assertEqual(validate_common_document(docs[0]), [])


if __name__ == "__main__":
    unittest.main()

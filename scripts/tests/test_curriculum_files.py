from __future__ import annotations

import unittest

from scripts.main.collectors.curriculum_files import (
    _clean_page_text, _is_visually_blank, parse_curriculum_attachments,
)


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


if __name__ == "__main__":
    unittest.main()

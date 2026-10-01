from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from scripts.crawlers import pknu_student_life as crawler
from scripts.crawlers.common.schema import attachment_text, validate_common_document


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

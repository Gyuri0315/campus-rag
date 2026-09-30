from __future__ import annotations

import unittest

from scripts.main.collectors.major_program import parse_major_program_page


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


if __name__ == "__main__":
    unittest.main()

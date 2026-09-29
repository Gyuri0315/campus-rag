from __future__ import annotations

import unittest
from pathlib import Path

from bs4 import BeautifulSoup

from scripts.crawlers.departments.adapters.numeric_cms import NumericCMSAdapter, extract_body_content


class NumericCMSTableTests(unittest.TestCase):
    def test_saved_ce_curriculum_fixture(self):
        fixture = Path(__file__).parent / 'fixtures/departments/numeric_cms/ce_ai_curriculum.html'
        soup = BeautifulSoup(fixture.read_text(encoding='utf-8'), 'lxml')
        parsed = NumericCMSAdapter().parse_static(soup, fallback_title='AI')
        self.assertGreater(len(parsed['content']), 1000)
        self.assertIn('2026', parsed['content'])
        self.assertIn(' | ', parsed['content'])
        self.assertGreater(len(parsed['content'].splitlines()), 10)

    def test_static_table_only_guide(self):
        soup = BeautifulSoup('''<main><table><caption>교육과정</caption>
          <tr><th>학년</th><th>과목</th><th>학점</th></tr>
          <tr><td rowspan="2">1학년</td><td>프로그래밍</td><td>3</td></tr>
          <tr><td>수학</td><td>2</td></tr></table></main>''', 'lxml')
        result = NumericCMSAdapter().parse_static(soup, fallback_title='교육과정')
        self.assertIn('학년 | 과목 | 학점', result['content'])
        self.assertIn('1학년 | 프로그래밍 | 3\n1학년 | 수학 | 2', result['content'])
        self.assertIn('교육과정', result['content'])

    def test_detail_keeps_tables_but_excludes_metadata_and_navigation(self):
        soup = BeautifulSoup('''<div class="a_bdCont"><table>
          <tr><th>작성자</th><td>관리자</td></tr>
          <tr><td class="bdvEdit">신청 안내<table>
            <tr><td>기간</td><td>9월</td></tr></table>문의 바랍니다</td></tr>
          </table><div class="c_bdvNav">이전 글</div></div>''', 'lxml')
        text = extract_body_content(soup)
        self.assertIn('기간 | 9월', text)
        self.assertIn('신청 안내', text)
        self.assertIn('문의 바랍니다', text)
        self.assertNotIn('관리자', text)
        self.assertNotIn('이전 글', text)

    def test_fallback_preserves_multiple_tables_and_colspan(self):
        soup = BeautifulSoup('''<main>안내<table><tr><td colspan="2">공통</td>
          <td>선택</td></tr><tr><td>A</td><td></td><td>B</td></tr></table>
          <table><tr><td>C</td><td>D</td></tr></table><script>hidden()</script></main>''', 'lxml')
        text = extract_body_content(soup)
        self.assertIn('공통 | 공통 | 선택', text)
        self.assertIn('A | | B', text)
        self.assertIn('C | D', text)
        self.assertNotIn('hidden', text)

    def test_nested_table_content_is_not_duplicated(self):
        soup = BeautifulSoup('<main><table><tr><td>외부<table><tr><td>내부</td></tr></table></td></tr></table></main>', 'lxml')
        self.assertEqual(extract_body_content(soup).count('내부'), 1)

    def test_empty_layout_container_before_real_body(self):
        soup = BeautifulSoup('<div class="container"></div><div id="sbCont"><table><tr><td>AI</td><td>3</td></tr></table></div>', 'lxml')
        self.assertEqual(NumericCMSAdapter().parse_static(soup, fallback_title='AI')['content'], 'AI | 3')

    def test_empty_body_states(self):
        from scripts.crawlers.departments.engine import validate_body_content, CrawlDiagnostics
        for state, status, failed in [('empty', 'failed', True), ('image_only', 'partial_success', False), ('attachment_only', 'success', False)]:
            with self.subTest(state=state):
                doc = {'content': '', 'metadata': {}, 'crawl': {'warnings': []}, 'source_id': 'test', 'url': 'https://example.test/page'}
                diagnostics = CrawlDiagnostics()
                self.assertEqual(validate_body_content(doc, {'content_state': state}, diagnostics), failed)
                self.assertEqual(doc['crawl']['status'], status)
                self.assertTrue(doc['crawl']['warnings'])
                self.assertEqual(bool(diagnostics.errors), state != 'attachment_only')

    def test_image_only_static(self):
        parsed = NumericCMSAdapter().parse_static(BeautifulSoup('<div class="container"></div><div id="sbCont"><h3 id="subTitle">guide</h3><img src="/guide.png"></div>', 'lxml'), fallback_title='guide')
        self.assertEqual(parsed['content_state'], 'image_only')

    def test_attachment_only_detail(self):
        parsed = NumericCMSAdapter().parse_detail(BeautifulSoup('<div class="bdvTitle">title</div><div class="a_bdCont"><div class="bdvEdit"></div><a href="/download/a.pdf">a.pdf</a></div>', 'lxml'), 'https://example.test/1', {}, base_url='https://example.test', site_prefix='ce')
        self.assertEqual(parsed['content_state'], 'attachment_only')


if __name__ == '__main__':
    unittest.main()

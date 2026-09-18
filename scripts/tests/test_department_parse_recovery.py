from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from bs4 import BeautifulSoup
from scripts.crawlers.departments.adapters.numeric_cms import NumericCMSAdapter
from scripts.crawlers.departments.adapters.query_view_do import QueryViewDoAdapter
from scripts.crawlers.departments import engine
from scripts.crawlers.departments.config import load_registry

ROOT = Path(__file__).parent / 'fixtures/departments'


class ParseRecoveryTests(unittest.TestCase):
    def test_redirected_university_detail_fixtures(self):
        for dataset in ('chinese', 'history', 'microbiology'):
            soup = BeautifulSoup((ROOT / 'numeric_cms' / f'redirect_{dataset}.html').read_text(encoding='utf-8'), 'lxml')
            result = NumericCMSAdapter().parse_detail(soup, 'https://www.pknu.ac.kr/main/163?action=view&no=1', {}, base_url='', site_prefix='')
            self.assertTrue(result['title'])
            self.assertTrue(result['body'] or result['attachments'])
            urls = [a['url'] for a in result['attachments']]
            self.assertEqual(len(urls), len(set(urls)))
            self.assertTrue(all(url.startswith('https://www.pknu.ac.kr/') for url in urls))

    def test_error_or_list_page_is_not_a_detail(self):
        for html in ('<title>로그인</title><p>접근 제한</p>', '<div class="bdCont"><td class="title_b">목록</td></div>'):
            self.assertIsNone(NumericCMSAdapter().parse_detail(BeautifulSoup(html, 'lxml'), 'https://www.pknu.ac.kr/main/163', {}, base_url='', site_prefix=''))

    def test_redirect_to_different_post_is_rejected(self):
        soup = BeautifulSoup((ROOT / 'numeric_cms/redirect_chinese.html').read_text(encoding='utf-8'), 'lxml')
        result = NumericCMSAdapter().parse_detail(soup, 'https://www.pknu.ac.kr/main/140?action=view&no=2',
            {'post_url': 'https://chinese.pknu.ac.kr/chinese/4290?action=view&no=1'}, base_url='', site_prefix='')
        self.assertIsNone(result)

    def test_gallery_fixture(self):
        soup = BeautifulSoup((ROOT / 'fishsci/gallery.html').read_text(encoding='utf-8'), 'lxml')
        items = QueryViewDoAdapter().parse_list(soup, 'https://fishsci.pknu.ac.kr/kor/view.do?no=46')
        self.assertEqual(len(items), 10)
        self.assertEqual(items[0]['source_id'], '1246')
        self.assertEqual(items[0]['date'], '2025-12-08')
        self.assertIn('pgMode=View', items[0]['post_url'])

    def test_failed_parse_does_not_advance_watermark(self):
        engine.configure_department(load_registry()['ce'])
        section = next(s for s in engine.SECTIONS if s['is_board'])
        item = {'post_url': 'https://ce.pknu.ac.kr/ce/1814?action=view&no=123', 'post_no': 123, 'is_notice': False}
        with patch.object(engine, 'fetch', return_value=SimpleNamespace(text='<html/>')), patch.object(engine, 'parse_list_page', return_value=[item]), patch.object(engine, 'parse_view_page', return_value=None):
            stats, watermark = engine.crawl_board(object(), section, {'items': {section['url']: {'last_no': 100}}}, False, recent_only=1)
        self.assertEqual(stats.failed, 1)
        self.assertEqual(watermark, 100)

    def test_retry_bypasses_existing_watermark(self):
        engine.configure_department(load_registry()['ce'])
        section = next(s for s in engine.SECTIONS if s['is_board'])
        item = {'post_url': 'https://ce.pknu.ac.kr/ce/1814?action=view&no=123', 'source_id': '123', 'retry_source_id': '2:123', 'post_no': None, 'is_notice': False}
        with patch.object(engine, 'fetch', return_value=SimpleNamespace(text='<html/>')) as fetch, patch.object(engine, 'parse_view_page', return_value=None):
            stats, watermark = engine.crawl_board(object(), section, {'items': {section['url']: {'last_no': 999}}}, False, retry_items=[item])
        self.assertEqual(stats.requested, 1)
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(watermark, 999)


if __name__ == '__main__':
    unittest.main()

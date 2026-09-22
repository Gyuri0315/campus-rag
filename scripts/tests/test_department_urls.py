import unittest
from types import SimpleNamespace
from unittest.mock import patch

from bs4 import BeautifulSoup
from scripts.crawlers.departments.urls import resolve_url
from scripts.crawlers.departments.access import detect_access_block
from scripts.crawlers.departments.adapters.numeric_cms import NumericCMSAdapter
from scripts.crawlers.departments.config import load_registry
from scripts.crawlers.departments import engine


class URLTests(unittest.TestCase):
    def test_rejects_invalid_urls_before_network(self):
        for url in ('', '#top', 'javascript:;', 'MAILTO:a@example.org', 'https://[broken', 'https://portal.pknu.ac.kr)？/', 'https://example.org:99999/a'):
            with self.subTest(url=url), patch.object(engine, 'get_with_tls_policy') as request:
                self.assertIsNone(engine.fetch(object(), url, delay=0))
                request.assert_not_called()
                self.assertFalse(engine._LAST_FETCH_FAILURE.retryable)
                self.assertIn(engine._LAST_FETCH_FAILURE.code, ('INVALID_URL', 'NON_FETCHABLE_URL'))

    def test_relative_urls_and_html_entities(self):
        base = 'https://pknujob.pknu.ac.kr/main/30'
        self.assertEqual(resolve_url('?action=view&amp;no=1', base), base+'?action=view&no=1')
        self.assertEqual(resolve_url('../file.pdf', base), 'https://pknujob.pknu.ac.kr/file.pdf')
        self.assertEqual(resolve_url('//www.pknu.ac.kr/a', base), 'https://www.pknu.ac.kr/a')

    def test_bad_attachment_links_do_not_break_document(self):
        soup = BeautifulSoup('<div class="bdvTitle">title</div><div class="a_bdCont"><div class="bdvEdit">body</div><a href="https://[broken">bad.pdf</a><a href="https://portal.pknu.ac.kr)？/">bad</a><a href="../file.pdf">good.pdf</a></div>', 'lxml')
        doc = NumericCMSAdapter().parse_detail(soup, 'https://example.org/ce/1', {}, base_url='https://example.org', site_prefix='ce')
        self.assertEqual(doc['body'], 'body')
        self.assertEqual([a['url'] for a in doc['attachments']], ['https://example.org/file.pdf'])

    def test_login_alert_is_distinct_from_public_login_link(self):
        html = "<script>alert('로그인 후 이용해주세요.'); window.location.href='/main/49';</script>"
        self.assertIn('login_required', detect_access_block(final_url='https://pknujob.pknu.ac.kr/main/32', html=html).reasons)
        self.assertFalse(detect_access_block(final_url='https://example.org', html='<a href="/main/49">로그인</a>').blocked)

    def test_document_exception_does_not_stop_next_document(self):
        engine.configure_department(load_registry()['ce'])
        section = next(s for s in engine.SECTIONS if s['is_board'])
        items = [{'post_url': f'https://ce.pknu.ac.kr/ce/1814?action=view&no={n}', 'post_no': n, 'is_notice': False} for n in (2, 1)]
        detail = {'title': 'ok', 'url': items[1]['post_url'], 'body': 'body', 'attachments': []}
        with patch.object(engine, 'fetch', return_value=SimpleNamespace(text='<html/>')), patch.object(engine, 'parse_list_page', return_value=items), patch.object(engine, 'parse_view_page', side_effect=[ValueError('bad link'), detail]) as parse, patch.object(engine, 'save_document'), patch.object(engine, 'load_existing_attachments', return_value=[]), patch.object(engine, 'save_attachments', return_value=[]):
            stats, watermark = engine.crawl_board(object(), section, {'items': {}}, True, recent_only=1)
        self.assertEqual(parse.call_count, 2)
        self.assertEqual(stats.failed, 1)
        self.assertEqual(stats.new, 1)
        self.assertEqual(watermark, 0)

    def test_list_uses_redirect_destination_as_base(self):
        engine.configure_department(load_registry()['ce'])
        section = next(s for s in engine.SECTIONS if s['is_board'])
        response = SimpleNamespace(text='<html/>', url='https://pknujob.pknu.ac.kr/main')
        with patch.object(engine, 'fetch', return_value=response), patch.object(engine, 'parse_list_page', return_value=[]) as parse:
            engine.crawl_board(object(), section, {'items': {}}, True, recent_only=1)
        self.assertEqual(parse.call_args.args[1], response.url)

    def test_login_response_has_nonretryable_code(self):
        response = SimpleNamespace(status_code=200, url='https://pknujob.pknu.ac.kr/main/32',
            text="<script>alert('로그인 후 이용해주세요.'); window.location.href='/main/49';</script>")
        with patch.object(engine, 'get_with_tls_policy', return_value=(response, False)):
            self.assertIsNone(engine.fetch(object(), response.url, delay=0))
        self.assertEqual(engine._LAST_FETCH_FAILURE.code, 'LOGIN_REQUIRED')
        self.assertFalse(engine._LAST_FETCH_FAILURE.retryable)


if __name__ == '__main__':
    unittest.main()

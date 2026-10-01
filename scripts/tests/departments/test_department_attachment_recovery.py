import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import requests
import json

from scripts.crawlers.departments import engine as e
from scripts.crawlers.departments.attachments import attachment_candidate, html_response
from scripts.crawlers.departments.config import load_registry


class AttachmentRecoveryTests(unittest.TestCase):
    def test_link_evidence(self):
        for url in ('https://zoom.us/download', 'https://example.org/profile', 'https://example.org/file/help'):
            self.assertFalse(attachment_candidate(url))
        for url in ('https://example.org/boardDownload.do?no=1', 'https://example.org/download?id=3', 'https://example.org/a.pdf?token=abc'):
            self.assertTrue(attachment_candidate(url))

    def test_html_sniffing(self):
        self.assertTrue(html_response('application/octet-stream', b'\xef\xbb\xbf <!DOCTYPE html><html>denied'))
        self.assertTrue(html_response('text/html', b'error'))
        self.assertFalse(html_response('application/pdf', b'%PDF-1.7'))

    def test_bounded_retry_and_no_403_retry(self):
        for code, expected in [('REQUEST_FAILED', 3), ('HTTP_503', 3), ('HTTP_403', 1)]:
            with patch.object(e, '_save_attachment_attempt', return_value=[{'url':'https://example.org/a', 'error':{'code':code}}]) as call, patch.object(e.time, 'sleep'):
                results=e.save_attachments(object(), [{'url':'https://example.org/a'}, {'url':'https://example.org/a'}], 'c', 's', 'https://example.org')
                self.assertEqual(call.call_count, expected)
                self.assertEqual(len(results), 1)

    def test_disposition_and_body_validation(self):
        e.configure_department(load_registry()['ce'])
        with tempfile.TemporaryDirectory() as temp:
            for body, expected in [(b'%PDF-1.7 content', True), (b'<html>login</html>', False)]:
                response=SimpleNamespace(status_code=200, url='https://example.org/download?id=1', headers={'Content-Type':'application/octet-stream','Content-Disposition':'attachment; filename="report.pdf"'}, iter_content=lambda **kw: iter([body]), close=lambda:None)
                with patch.object(e,'ensure_file_dir',return_value=Path(temp)), patch.object(e,'PROJECT_ROOT',Path(temp)), patch.object(e,'get_with_tls_policy',return_value=(response,False)), patch.object(e.time,'sleep'):
                    result=e.save_attachments(object(),[{'url':response.url}], 'c','s','https://example.org')[0]
                self.assertEqual(result['downloaded'],expected)
                if expected:self.assertEqual(result['name'],'report.pdf')
                else:self.assertEqual(result['error']['code'],'UNSUPPORTED_CONTENT_TYPE')

    def test_section_exception_preserves_attachment_counts(self):
        e.configure_department(load_registry()['ce'])
        section=next(s for s in e.SECTIONS if s['is_board'])
        def broken(*args, **kw):
            kw['accumulator'].attachments_failed=2
            kw['accumulator'].attachments_discovered=2
            raise ValueError('later page broke')
        with patch.object(e,'SECTIONS',[section]), patch.object(e,'load_state',return_value={'items':{}}), patch.object(e,'save_state'), patch.object(e,'build_session'), patch.object(e,'crawl_board',side_effect=broken):
            stats=e.run_crawl()
        self.assertEqual(stats.attachments_failed,2)
        self.assertEqual(stats.failed,1)

    def test_retry_command_preserves_success_and_deduplicates_failures(self):
        from scripts.crawlers.departments.retry_attachments import retry
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            good={'id':'attachment-001','url':'https://example.org/good.pdf','downloaded':True,'saved_path':'good.pdf','error':None}
            bad={'id':'attachment-002','url':'https://example.org/bad.pdf','name':'bad.pdf','downloaded':False,'error':{'code':'HTTP_403'}}
            path=root/'doc.json'
            path.write_text(json.dumps({'url':'https://example.org/post','category':'test','slug':'test','attachments':[good,bad,{**bad,'id':'attachment-003'}]}),encoding='utf-8')
            (root/'good.pdf').write_bytes(b'unchanged')
            with patch.object(e,'configure_department'), patch.object(e,'PATHS',SimpleNamespace(json=root)), patch.object(e,'build_session'), patch.object(e,'fetch',return_value=SimpleNamespace(url='https://example.org/post')), patch.object(e,'save_attachments',return_value=[{**bad,'downloaded':True,'error':None}]) as download, patch('scripts.crawlers.common.schema.RunResult.save',return_value=root/'receipt.json'):
                retry('ce',20,document=path)
            download.assert_called_once()
            result=json.loads(path.read_text(encoding='utf-8'))
            self.assertEqual(result['attachments'][0],good)
            self.assertTrue(result['attachments'][2]['downloaded'])
            self.assertEqual((root/'good.pdf').read_bytes(),b'unchanged')


if __name__ == '__main__':unittest.main()

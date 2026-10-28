import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import requests
import json

from scripts.crawlers.departments import engine as e
from scripts.crawlers.departments.attachments import attachment_candidate, html_response, board_reference
from scripts.crawlers.departments.config import load_registry


class AttachmentRecoveryTests(unittest.TestCase):
    def test_static_embedded_pdf_download_and_metadata_only_modes(self):
        e.configure_department(load_registry()['ce'])
        url='https://ce.pknu.ac.kr/ce/1817'
        response=SimpleNamespace(text='<div id="sbCont"><iframe src="/upload/support.pdf"></iframe></div>',url=url)
        section={'id':'support','url':url,'category':'support','name':'Support','type':'guide'}
        attachment={'url':'https://ce.pknu.ac.kr/upload/support.pdf','name':'support.pdf',
                    'downloaded':True,'saved_path':'files/support.pdf','error':None}
        for metadata_only in (False,True):
            with (patch.object(e,'fetch',return_value=response),
                  patch.object(e,'collect_document_images'),
                  patch.object(e,'existing_document_path',return_value=Path('__missing_document__.json')),
                  patch.object(e,'save_document') as save,
                  patch.object(e,'save_attachments',return_value=[attachment]) as download):
                stats=e.crawl_static(object(),section,no_download_files=metadata_only)
                doc=save.call_args.args[0]
                self.assertEqual(stats.attachments_discovered,1)
                self.assertEqual(stats.attachments_downloaded,0 if metadata_only else 1)
                self.assertEqual(doc['attachments'][0]['downloaded'],not metadata_only)
                if metadata_only: download.assert_not_called()
                else: self.assertEqual(download.call_args.args[1][0]['url'],attachment['url'])

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
            with patch.object(e,'configure_department'), patch('scripts.crawlers.departments.retry_attachments.get_dataset_paths',return_value=SimpleNamespace(json=root,runs=root/'runs',html=root/'html')), patch.object(e,'build_session'), patch.object(e,'fetch',return_value=SimpleNamespace(url='https://example.org/post')), patch.object(e,'save_attachments',return_value=[{**bad,'downloaded':True,'error':None}]) as download:
                retry('ce',20,error_code='HTTP_403',document=path)
            download.assert_called_once()
            result=json.loads(path.read_text(encoding='utf-8'))
            self.assertEqual(result['attachments'][0],good)
            self.assertTrue(result['attachments'][2]['downloaded'])
            self.assertEqual((root/'good.pdf').read_bytes(),b'unchanged')

    def test_post_link_is_not_a_file_even_with_a_file_label(self):
        url = 'https://cms.pknu.ac.kr/korean/view.do?no=1353&idx=475324&view=view'
        self.assertTrue(board_reference(url))
        self.assertFalse(attachment_candidate(url, '채용공고.hwp'))
        self.assertFalse(attachment_candidate('https://icms.pknu.ac.kr/builder/board/edit.do?action=list&no=1', 'file.pdf'))
        self.assertTrue(attachment_candidate('https://geoinfo.pknu.ac.kr/program/amibbs/bbsExe.php?mode=down&idx=1'))
        self.assertFalse(attachment_candidate('https://geoinfo.pknu.ac.kr/program/amibbs/bbsExe.php?mode=list&idx=1'))

    def test_supported_download_reference_can_be_retried_without_losing_metadata(self):
        from scripts.crawlers.departments.retry_attachments import _restore_attachment_references
        old={'id':'attachment-001','name':'bbsExe.php',
             'url':'https://geoinfo.pknu.ac.kr/program/amibbs/bbsExe.php?mode=down&idx=1',
             'downloaded':False,'error':{'code':'UNSUPPORTED_CONTENT_TYPE'}}
        doc={'attachments':[],'reference_links':[{'source':'reclassified_attachment','attachment_metadata':old},
                                                {'url':'https://example.org/news','label':'News'}]}
        _restore_attachment_references(doc,{'UNSUPPORTED_CONTENT_TYPE'})
        self.assertEqual(doc['attachments'],[old])
        self.assertEqual(doc['reference_links'],[{'url':'https://example.org/news','label':'News'}])

    def test_real_html_attachment_preserves_bytes_and_hash(self):
        import hashlib
        e.configure_department(load_registry()['ce'])
        for body in (b'<!doctype html><html><body>Announcement</body></html>',
                     b'<table><tr><td>Newsletter</td></tr></table>'):
            with self.subTest(body=body), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                response = SimpleNamespace(
                    status_code=200, url='https://example.org/boardDownload.do?no=1',
                    headers={'Content-Type':'application/octet-stream',
                             'Content-Disposition':'attachment; filename="notice.html"'},
                    iter_content=lambda **kw: iter([body]), close=lambda:None)
                with patch.object(e,'ensure_file_dir',return_value=root), patch.object(e,'PROJECT_ROOT',root), patch.object(e,'get_with_tls_policy',return_value=(response,False)), patch.object(e.time,'sleep'):
                    result = e.save_attachments(object(), [{'url':response.url}], 'c','s','https://example.org/post')[0]
                self.assertTrue(result['downloaded'])
                self.assertEqual((root/result['saved_path']).read_bytes(),body)
                self.assertEqual(result['sha256'],hashlib.sha256(body).hexdigest())
                self.assertEqual(result['size_bytes'],len(body))
                self.assertEqual(result['content_type'],'text/html')
                self.assertEqual(result['representation'],'html_attachment')

    def test_login_and_missing_file_html_are_not_saved_as_attachments(self):
        e.configure_department(load_registry()['ce'])
        cases = [(b'<html><form><input type="password"></form></html>','LOGIN_REQUIRED'),
                 (b'<html>Could not get file name: report.xls</html>','FILE_UNAVAILABLE')]
        for body, code in cases:
            with self.subTest(code=code), tempfile.TemporaryDirectory() as temp:
                root=Path(temp)
                response=SimpleNamespace(status_code=200,url='https://example.org/boardDownload.do?no=1',
                    headers={'Content-Type':'text/html','Content-Disposition':'attachment; filename="report.html"'},
                    iter_content=lambda **kw:iter([body]),close=lambda:None)
                with patch.object(e,'ensure_file_dir',return_value=root),patch.object(e,'PROJECT_ROOT',root),patch.object(e,'get_with_tls_policy',return_value=(response,False)),patch.object(e.time,'sleep'):
                    result=e.save_attachments(object(),[{'url':response.url}],'c','s','https://example.org/post')[0]
                self.assertFalse(result['downloaded'])
                self.assertEqual(result['error']['code'],code)
                self.assertEqual(list(root.iterdir()),[])

    def test_attachment_timeout_is_separate_and_dns_failure_is_not_repeated(self):
        e.configure_department(load_registry()['ce'])
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(e,'ensure_file_dir',return_value=Path(temp)),patch.object(e.time,'sleep'),patch.object(e,'get_with_tls_policy',side_effect=requests.ConnectionError('NameResolutionError: getaddrinfo failed')) as call:
                result=e.save_attachments(object(),[{'url':'https://example.org/file.pdf'}],'c','s','https://example.org/post')[0]
            self.assertEqual(call.call_count,1)
            self.assertEqual(call.call_args.kwargs['timeout'],(10,60))
            self.assertEqual(result['error']['code'],'DNS_RESOLUTION_FAILED')

    def test_default_retry_defers_403_and_keeps_source_document_unchanged(self):
        from scripts.crawlers.departments.retry_attachments import retry
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); path=root/'doc.json'
            raw=json.dumps({'attachments':[{'url':'https://example.org/blocked.pdf','downloaded':False,'error':{'code':'HTTP_403'}}]}).encode()
            path.write_bytes(raw)
            with patch('scripts.crawlers.departments.retry_attachments.get_dataset_paths',return_value=SimpleNamespace(json=root,runs=root/'runs',html=root/'html')),patch.object(e,'build_session') as session:
                self.assertEqual(retry('ce',document=path),0)
            session.assert_not_called()
            self.assertEqual(path.read_bytes(),raw)

    def test_reference_reclassification_keeps_original_metadata_and_body(self):
        from scripts.crawlers.departments.retry_attachments import retry
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); path=root/'doc.json'
            old={'id':'attachment-001','name':'News','url':'https://example.org/news','downloaded':False,
                 'error':{'code':'UNSUPPORTED_CONTENT_TYPE','message':'text/html','retryable':False}}
            doc={'url':'https://example.org/post','content':'preserved body','content_hash':'unchanged',
                 'attachments':[old],'category':'test','slug':'test'}
            raw=json.dumps(doc).encode();path.write_bytes(raw)
            with patch('scripts.crawlers.departments.retry_attachments.get_dataset_paths',return_value=SimpleNamespace(json=root,runs=root/'runs',html=root/'html')),patch.object(e,'configure_department'),patch.object(e,'build_session'),patch.object(e,'fetch') as fetch:
                retry('ce',document=path)
            fetch.assert_not_called()
            result=json.loads(path.read_text(encoding='utf-8'))
            self.assertEqual(result['attachments'],[])
            self.assertEqual(result['content'],'preserved body')
            self.assertEqual(result['content_hash'],'unchanged')
            self.assertEqual(result['reference_links'][0]['attachment_metadata'],old)
            backup=next((root/'runs').rglob('documents_before/doc.json'))
            self.assertEqual(backup.read_bytes(),raw)

    def test_legacy_cms_reference_keeps_snapshot_without_following_scripts(self):
        from scripts.crawlers.departments.retry_attachments import _resolve_reference
        raw=b"<script>location.href='/application.jsp';</script>"
        old={'id':'attachment-001','name':'file.hwp',
             'url':'https://cms.pknu.ac.kr/dept/view.do?idx=1&view=view',
             'error':{'code':'TLS_ERROR'}}
        response=SimpleNamespace(content=raw,text=raw.decode(),url=old['url'],close=lambda:None)
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            with patch.object(e,'PROJECT_ROOT',root),patch.object(e,'fetch',return_value=response) as fetch:
                files,ref=_resolve_reference(object(),old,root/'recovery')
            fetch.assert_called_once()
            self.assertEqual(files,[])
            self.assertEqual(ref['status'],'no_file_links')
            self.assertEqual((root/ref['snapshot']['saved_path']).read_bytes(),raw)
            self.assertEqual(ref['attachment_metadata'],old)

    def test_legacy_reference_discovers_only_files_in_detail_container(self):
        from scripts.crawlers.departments.retry_attachments import _resolve_reference
        old={'id':'attachment-001','name':'file.hwp',
             'url':'https://cms.pknu.ac.kr/dept/view.do?idx=1&view=view'}
        raw=b'<a href="/outside.pdf">Outside</a><div class="a_bdCont"><a href="/boardDownload.do?no=1">file.hwp</a></div>'
        response=SimpleNamespace(content=raw,text=raw.decode(),url=old['url'],close=lambda:None)
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            with patch.object(e,'PROJECT_ROOT',root),patch.object(e,'fetch',return_value=response):
                files,ref=_resolve_reference(object(),old,root/'recovery')
            self.assertEqual([f['url'] for f in files],['https://cms.pknu.ac.kr/boardDownload.do?no=1'])
            self.assertEqual(ref['status'],'resolved_files')


if __name__ == '__main__':unittest.main()

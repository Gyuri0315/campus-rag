"""Attachment evidence shared by department link and response parsing."""
import re
from urllib.parse import urlsplit, parse_qs, unquote

EXTENSIONS = r'\.(pdf|hwp|hwpx|doc|docx|xls|xlsx|ppt|pptx|zip|rar|7z|txt|csv|png|jpg|jpeg|gif)$'
HTML_EXTENSIONS = ('.html', '.htm', '.shtml')


def board_reference(href: str) -> bool:
    """A linked CMS post/editor is a page even when its label looks like a file."""
    try:
        parsed = urlsplit(href)
        query = {k.lower(): [str(v).lower() for v in values]
                 for k, values in parse_qs(parsed.query).items()}
    except ValueError:
        return False
    return (query.get('action') == ['view'] or query.get('view') == ['view']
            or query.get('pgmode') == ['view']
            or parsed.path.lower().endswith('/builder/board/edit.do'))


def attachment_candidate(href: str, name: str = '') -> bool:
    try:
        parsed = urlsplit(href)
    except ValueError:
        return False
    if parsed.scheme and parsed.scheme.lower() not in {'http', 'https'}:
        return False
    if board_reference(href):
        return False
    path = unquote(parsed.path).lower()
    if re.search(EXTENSIONS, path) or re.search(EXTENSIONS, name.lower().strip()):
        return True
    endpoint = path.rsplit('/', 1)[-1].lower()
    query = {k.lower(): v for k, v in parse_qs(parsed.query).items()}
    if endpoint == 'bbsexe.php' and query.get('mode') == ['down']:
        return bool(query.get('idx'))
    if endpoint in {'boarddownload.do', 'boarddown.do'}:
        return any(query.get(k) for k in ('no', 'fkey', 'fileno', 'fileid'))
    if endpoint in {'download', 'downloadfile', 'download.do', 'filedown.do', 'download.php', 'downloadfile.do'}:
        return any(query.get(k) for k in ('id', 'fileid', 'fileuid', 'fileno', 'file', 'filename', 'atchfileid'))
    return False


def html_response(content_type: str, prefix: bytes) -> bool:
    if 'text/html' in content_type or 'application/xhtml+xml' in content_type:
        return True
    text = prefix.lstrip(b'\xef\xbb\xbf\x00\t\r\n ').lower()
    return bool(re.match(br'(?:<!doctype\s+html|<html\b|<head\b|<body\b|<script\b)', text))


def explicit_html_attachment(filename: str, content_disposition: str) -> bool:
    """Require server-provided attachment evidence before preserving HTML bytes."""
    return (filename.lower().endswith(HTML_EXTENSIONS)
            and bool(re.match(r'\s*attachment(?:\s*;|\s*$)', content_disposition, re.I)))


def html_download_error(prefix: bytes, final_url: str) -> str | None:
    from bs4 import BeautifulSoup
    from scripts.crawlers.departments.access import detect_access_block

    html = prefix.decode('utf-8', errors='replace')
    evidence = detect_access_block(final_url=final_url, html=html)
    soup = BeautifulSoup(html, 'lxml')
    if 'login_required' in evidence.reasons or soup.select_one('input[type="password"]'):
        return 'LOGIN_REQUIRED'
    if evidence.blocked:
        return 'ACCESS_BLOCKED'
    text = soup.get_text(' ', strip=True).lower()
    if len(text) < 2000 and any(phrase in text for phrase in (
        'could not get file name', 'file not found', '파일이 존재하지',
        '파일을 찾을 수 없', '파일 정보가 없', '첨부파일이 없습니다',
    )):
        return 'FILE_UNAVAILABLE'
    return None

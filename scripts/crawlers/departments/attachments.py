"""Attachment evidence shared by department link and response parsing."""
import re
from urllib.parse import urlsplit, parse_qs, unquote

EXTENSIONS = r'\.(pdf|hwp|hwpx|doc|docx|xls|xlsx|ppt|pptx|zip|rar|7z|txt|csv|png|jpg|jpeg|gif)$'


def attachment_candidate(href: str, name: str = '') -> bool:
    try:
        parsed = urlsplit(href)
    except ValueError:
        return False
    if parsed.scheme and parsed.scheme.lower() not in {'http', 'https'}:
        return False
    path = unquote(parsed.path).lower()
    if re.search(EXTENSIONS, path) or re.search(EXTENSIONS, name.lower().strip()):
        return True
    endpoint = path.rsplit('/', 1)[-1].lower()
    query = {k.lower(): v for k, v in parse_qs(parsed.query).items()}
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

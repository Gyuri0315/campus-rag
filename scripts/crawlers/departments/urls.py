"""Validate and resolve crawl links without guessing malformed destinations."""
from html import unescape
import logging
import re
from urllib.parse import urljoin, urlsplit, urlunsplit

from scripts.crawlers.common.logging import log_event


def resolve_url(value: object, base: str = "") -> str:
    raw = unescape(str(value or "")).strip()
    if not raw or raw.startswith('#'):
        raise ValueError("NON_FETCHABLE_URL: empty or fragment-only link")
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", raw) and not raw.lower().startswith(('http:', 'https:')):
        raise ValueError("NON_FETCHABLE_URL: unsupported scheme")
    if any(ord(char) < 32 for char in raw):
        raise ValueError("INVALID_URL: control character")
    try:
        parts = urlsplit(urljoin(base, raw))
        if parts.scheme not in ('http', 'https') or not parts.hostname or parts.username or parts.password:
            raise ValueError("missing HTTP host or embedded credentials")
        _ = parts.port
        host = parts.hostname
        if any(char.isspace() for char in host) or '\\' in parts.netloc:
            raise ValueError("invalid host characters")
        if ':' not in host and not re.fullmatch(r'[A-Za-z0-9.-]+', host.encode('idna').decode('ascii')):
            raise ValueError("invalid hostname")
        return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ''))
    except (ValueError, UnicodeError) as exc:
        raise ValueError(f"INVALID_URL: {exc}") from exc


def resolve_link(value: object, base: str) -> str:
    try:
        return resolve_url(value, base)
    except ValueError as exc:
        log_event(logging.getLogger('crawler.department'), logging.WARNING, 'url_skipped',
                  reason=str(exc), error_code=str(exc).split(':', 1)[0], retryable=False)
        return ''

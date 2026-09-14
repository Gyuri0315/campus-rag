"""Safe, bounded HTML redirect handling for legacy department sites."""

from __future__ import annotations

import re
from urllib.parse import urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup


def meta_refresh_target(html: str, current_url: str) -> str | None:
    """Return a validated same-host HTTP(S) meta-refresh URL, if present."""
    soup = BeautifulSoup(html, "lxml")
    element = next((meta for meta in soup.select("meta[http-equiv][content]")
                    if str(meta.get("http-equiv") or "").strip().lower() == "refresh"), None)
    if not element:
        return None
    match = re.search(r"(?:^|;)\s*url\s*=\s*(['\"]?)(.*?)\1\s*$",
                      str(element.get("content") or ""), re.IGNORECASE)
    if not match or not match.group(2).strip():
        raise ValueError("invalid meta refresh URL")
    target = urljoin(current_url, match.group(2).strip())
    source, parsed = urlsplit(current_url), urlsplit(target)
    if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
        raise ValueError("unsafe meta refresh URL")
    if not source.hostname or (parsed.hostname or "").lower() != source.hostname.lower():
        raise ValueError("external meta refresh blocked")
    normalized_path = re.sub(r"/{2,}", "/", parsed.path)
    return urlunsplit((parsed.scheme, parsed.netloc, normalized_path, parsed.query, ""))


def follow_meta_refresh_once(session, response, *, timeout: int, system_trust_fallback: bool = False):
    """Follow at most one validated meta refresh and never recurse."""
    from scripts.crawlers.departments.tls import get_with_tls_policy

    target = meta_refresh_target(response.text, response.url)
    if not target:
        return response, False
    followed, _ = get_with_tls_policy(session, target, timeout=timeout, allow_redirects=True,
                                      system_trust_fallback=system_trust_fallback)
    followed.raise_for_status()
    followed.encoding = followed.apparent_encoding or "utf-8"
    return followed, True

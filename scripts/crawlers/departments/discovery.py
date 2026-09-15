"""Discover candidate menu and board configuration from compatible CMS sites."""

from __future__ import annotations

import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlsplit

import requests
from bs4 import BeautifulSoup

from scripts.crawlers.common.schema import now_kst
from scripts.crawlers.departments.tls import classify_request_error, get_with_tls_policy
from scripts.crawlers.departments.adapters import get_adapter
from scripts.crawlers.departments.redirects import follow_meta_refresh_once
from scripts.crawlers.departments.access import ACCESS_BLOCKED, detect_access_block


@dataclass(frozen=True)
class DiscoveredSection:
    id: str
    name: str
    category: str
    kind: str
    path: str
    document_type: str
    bbs_id: str | None
    confidence: float
    status: str = "candidate"
    warnings: list[str] = field(default_factory=list)
    final_url: str | None = None


@dataclass(frozen=True)
class DiscoveryResult:
    site_key: str
    base_url: str
    status: str
    site_prefix: str | None
    sections: list[DiscoveredSection]
    errors: list[dict[str, Any]]
    discovered_at: str = field(default_factory=now_kst)

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["sections"] = [asdict(section) for section in self.sections]
        return result


def extract_bbs_id(html: str, page_url: str) -> str | None:
    from scripts.crawlers.departments.adapters.numeric_cms import extract_bbs_id as extract
    return extract(html, page_url)


def _legacy_extract_bbs_id(html: str, page_url: str) -> str | None:
    soup = BeautifulSoup(html, "lxml")
    field = soup.select_one('input[name="bbsId"], input[name="bbs_id"]')
    if field and str(field.get("value") or "").isdigit():
        return str(field.get("value"))
    patterns = (
        r"(?i)bbsId\s*[=:]\s*['\"]?(\d+)",
        r"(?i)bbs_id\s*[=:]\s*['\"]?(\d+)",
        r"(?i)[?&]bbsId=(\d+)",
    )
    for pattern in patterns:
        match = re.search(pattern, html)
        if match:
            return match.group(1)
    for anchor in soup.select("a[href]"):
        query = parse_qs(urlsplit(str(anchor.get("href") or "")).query)
        for key in ("bbsId", "bbs_id"):
            value = query.get(key, [None])[0]
            if value and str(value).isdigit():
                return str(value)
    query = parse_qs(urlsplit(page_url).query)
    value = query.get("bbsId", [None])[0]
    return str(value) if value and str(value).isdigit() else None


def analyze_section_html(
    *, name: str, page_url: str, html: str, final_url: str | None = None,
    adapter_name: str = "numeric_cms",
) -> DiscoveredSection:
    payload = get_adapter(adapter_name).analyze_section(
        name=name, page_url=page_url, html=html, final_url=final_url,
    )
    return DiscoveredSection(**payload)


def _legacy_analyze_section_html(
    *, name: str, page_url: str, html: str, final_url: str | None = None,
) -> DiscoveredSection:
    soup = BeautifulSoup(html, "lxml")
    parsed = urlsplit(page_url)
    menu_id = parsed.path.rstrip("/").rsplit("/", 1)[-1]
    effective_url = final_url or page_url
    effective = urlsplit(effective_url)
    if effective.hostname and effective.hostname.lower() != (parsed.hostname or "").lower():
        return DiscoveredSection(
            id=f"menu_{menu_id}", name=name or f"menu-{menu_id}", category="미분류",
            kind="static_page", path=parsed.path.rstrip("/"), document_type="guide",
            bbs_id=None, confidence=1.0, status="unsupported",
            warnings=[f"external redirect: {effective_url}"], final_url=effective_url,
        )
    if menu_id in {"1", "9999"} or name.strip().upper() in {"HOME", "SITEMAP"}:
        return DiscoveredSection(
            id=f"menu_{menu_id}", name=name or f"menu-{menu_id}", category="미분류",
            kind="static_page", path=parsed.path.rstrip("/"), document_type="guide",
            bbs_id=None, confidence=1.0, status="unsupported",
            warnings=["navigation-only menu"], final_url=effective_url,
        )
    board_list = bool(soup.select_one(".a_brdList"))
    view_links = any("action=view" in str(anchor.get("href") or "").lower() for anchor in soup.select("a[href]"))
    bbs_id = extract_bbs_id(html, page_url)
    is_board = board_list or view_links or bbs_id is not None
    page_text = soup.get_text(" ", strip=True)
    faculty_markers = sum(
        marker in page_text for marker in ("이메일", "전화번호", "연구분야", "직명", "상세보기")
    )
    faculty_profile = "교수" in name and bbs_id is None and faculty_markers >= 2
    if faculty_profile:
        # Faculty cards often use action=view for profile details. They are a
        # static directory, not a CMS board, when no bbs_id is present.
        is_board = False
    has_content = has_content_container(soup)
    warnings: list[str] = []
    confidence = 0.9 if faculty_profile else (0.95 if board_list and bbs_id else (0.75 if is_board else (0.65 if has_content else 0.2)))
    status = "candidate" if is_board or has_content or faculty_profile else "unsupported"
    if is_board and not bbs_id:
        warnings.append("board-like page has no discoverable bbs_id")
    if not name:
        name = f"menu-{menu_id}"
        warnings.append("menu name was empty")
    return DiscoveredSection(
        id=f"menu_{menu_id}", name=name, category="미분류",
        kind="board" if is_board else "static_page", path=parsed.path.rstrip("/"),
        document_type="notice" if is_board else "guide", bbs_id=bbs_id,
        confidence=confidence, status=status, warnings=warnings,
        final_url=effective_url,
    )


def discover_from_html(
    *, site_key: str, base_url: str, homepage_html: str,
    section_html: dict[str, str], section_final_urls: dict[str, str] | None = None,
    adapter_name: str = "numeric_cms",
) -> DiscoveryResult:
    adapter = get_adapter(adapter_name)
    menus = adapter.discover_menus(homepage_html, base_url)
    sections = [
        DiscoveredSection(**adapter.analyze_section(
            name=menu["name"], page_url=menu["url"], html=section_html.get(menu["url"], ""),
            final_url=(section_final_urls or {}).get(menu["url"]),
        ))
        for menu in menus
    ]
    prefix = None
    if menus:
        first_url = urlsplit(menus[0]["url"])
        if adapter_name == "numeric_cms":
            prefix = first_url.path.strip("/").split("/", 1)[0]
        else:
            # Query/PHP CMS sites live at the host root; using ``view.do`` or a
            # PHP directory as a site prefix corrupts registry configuration.
            prefix = (first_url.hostname or "").split(".", 1)[0] or None
    candidates = [section for section in sections if section.status == "candidate"]
    return DiscoveryResult(
        site_key=site_key, base_url=base_url,
        status="success" if candidates else "no_candidates", site_prefix=prefix,
        sections=sections, errors=[],
    )


def discover_site(
    *, site_key: str, base_url: str, session: requests.Session | None = None,
    timeout: int = 20, max_sections: int = 50, request_delay: float = 0.2,
    system_trust_fallback: bool = False,
    adapter_name: str = "numeric_cms",
) -> DiscoveryResult:
    client = session or requests.Session()
    errors: list[dict[str, Any]] = []
    try:
        homepage, _ = get_with_tls_policy(client, base_url, timeout=timeout, allow_redirects=True,
                                          system_trust_fallback=system_trust_fallback)
        homepage.raise_for_status()
        homepage.encoding = "utf-8"
        homepage, _ = follow_meta_refresh_once(
            client, homepage, timeout=timeout,
            system_trust_fallback=system_trust_fallback,
        )
        access = detect_access_block(final_url=homepage.url, html=homepage.text)
        if access.blocked:
            return DiscoveryResult(
                site_key=site_key, base_url=homepage.url, status="blocked", site_prefix=None,
                sections=[], errors=[{"code": ACCESS_BLOCKED, "url": homepage.url,
                                     "message": f"access denial page: {', '.join(access.reasons)}",
                                     "retryable": False}],
            )
    except (requests.RequestException, ValueError) as exc:
        return DiscoveryResult(
            site_key=site_key, base_url=base_url, status="failed", site_prefix=None,
            sections=[], errors=[{"code": classify_request_error(exc) if isinstance(exc, requests.RequestException) else "META_REFRESH_BLOCKED",
                                  "url": base_url, "message": str(exc), "retryable": isinstance(exc, requests.RequestException)}],
        )
    adapter = get_adapter(adapter_name)
    menus = adapter.discover_menus(homepage.text, homepage.url)[:max_sections]
    html_by_url: dict[str, str] = {}
    final_urls: dict[str, str] = {}
    for index, menu in enumerate(menus):
        if index and request_delay > 0:
            time.sleep(request_delay)
        try:
            response, _ = get_with_tls_policy(client, menu["url"], timeout=timeout, allow_redirects=True,
                                              system_trust_fallback=system_trust_fallback)
            response.raise_for_status()
            response.encoding = "utf-8"
            html_by_url[menu["url"]] = response.text
            final_urls[menu["url"]] = response.url
        except requests.RequestException as exc:
            errors.append({
                "code": classify_request_error(exc), "url": menu["url"],
                "message": str(exc), "retryable": True,
            })
    result = discover_from_html(
        site_key=site_key, base_url=homepage.url,
        homepage_html=homepage.text, section_html=html_by_url,
        section_final_urls=final_urls,
        adapter_name=adapter_name,
    )
    return DiscoveryResult(
        site_key=result.site_key, base_url=result.base_url,
        status="partial_success" if errors and result.sections else ("failed" if errors else result.status),
        site_prefix=result.site_prefix, sections=result.sections, errors=errors,
    )

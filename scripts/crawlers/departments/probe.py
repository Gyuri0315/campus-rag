"""Read-only CMS fingerprint probes for department homepages."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import parse_qs, urljoin, urlsplit

import requests
from bs4 import BeautifulSoup

from scripts.crawlers.common.schema import now_kst
from scripts.crawlers.departments.tls import classify_request_error, get_with_tls_policy
from scripts.crawlers.departments.redirects import follow_meta_refresh_once
from scripts.crawlers.departments.access import ACCESS_BLOCKED, detect_access_block


CONTENT_CONTAINER_SELECTOR = (
    "#contents, .contents, main, .a_bdCont, #container, .containerWrap, .container"
)


def select_content_container(soup: BeautifulSoup):
    """Return the common CMS content root, including hub-style PKNU layouts."""
    return soup.select_one(CONTENT_CONTAINER_SELECTOR)


def has_content_container(soup: BeautifulSoup) -> bool:
    return select_content_container(soup) is not None


@dataclass(frozen=True)
class ProbeResult:
    site_key: str
    requested_url: str
    final_url: str | None
    status: str
    compatible: bool
    confidence: float
    http_status: int | None
    fingerprints: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    error: dict[str, Any] | None = None
    probed_at: str = field(default_factory=now_kst)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def numeric_menu_links(html: str, base_url: str) -> list[dict[str, str]]:
    soup = BeautifulSoup(html, "lxml")
    base = urlsplit(base_url)
    found: dict[str, dict[str, str]] = {}
    for anchor in soup.select("a[href]"):
        absolute = urljoin(base_url, str(anchor.get("href") or ""))
        parsed = urlsplit(absolute)
        if parsed.netloc.lower() != base.netloc.lower():
            continue
        if not re.fullmatch(r"/[A-Za-z0-9_-]+/\d+/?", parsed.path):
            continue
        clean_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path.rstrip('/')}"
        found.setdefault(clean_url, {
            "name": anchor.get_text(" ", strip=True), "url": clean_url,
            "path": parsed.path.rstrip("/"),
        })
    return list(found.values())


def mcode_menu_links(html: str, base_url: str) -> list[dict[str, str]]:
    """Extract same-origin query menus identified by ``mcode``/``menucode``."""
    soup, base = BeautifulSoup(html, "lxml"), urlsplit(base_url)
    found: dict[str, dict[str, str]] = {}
    for anchor in soup.select("a[href]"):
        absolute = urljoin(base_url, str(anchor.get("href") or ""))
        parsed, query = urlsplit(absolute), parse_qs(urlsplit(absolute).query)
        code = str((query.get("mcode") or query.get("menucode") or [""])[0])
        if parsed.netloc.lower() != base.netloc.lower() or not code or query.get("no"):
            continue
        found.setdefault(code, {"name": anchor.get_text(" ", strip=True), "url": absolute, "mcode": code})
    return list(found.values())


def query_menu_links(html: str, base_url: str) -> list[dict[str, str]]:
    """Find same-host ``view.do?no=`` menus used by non-numeric PKNU CMS sites."""
    soup = BeautifulSoup(html, "lxml")
    base = urlsplit(base_url)
    found: dict[str, dict[str, str]] = {}
    for anchor in soup.select("a[href]"):
        absolute = urljoin(base_url, str(anchor.get("href") or ""))
        parsed = urlsplit(absolute)
        if parsed.netloc.lower() != base.netloc.lower() or not parsed.path.endswith("/view.do"):
            continue
        menu_no = parse_qs(parsed.query).get("no", [None])[0]
        if not menu_no or not str(menu_no).isdigit():
            continue
        clean_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}?no={menu_no}"
        found.setdefault(clean_url, {
            "name": anchor.get_text(" ", strip=True), "url": clean_url,
            "menu_no": str(menu_no),
        })
    return list(found.values())


def query_board_item_links(html: str, page_url: str) -> list[dict[str, str]]:
    """Extract stable item IDs from ``pgMode=View&idx=`` board links."""
    soup = BeautifulSoup(html, "lxml")
    found: dict[str, dict[str, str]] = {}
    for anchor in soup.select("a[href]"):
        absolute = urljoin(page_url, str(anchor.get("href") or ""))
        query = parse_qs(urlsplit(absolute).query)
        mode = str(query.get("pgMode", [""])[0]).lower()
        item_id = str(query.get("idx", [""])[0])
        if not item_id:
            match = re.search(r"moveView\(['\"]?(\d+)", str(anchor.get("onclick") or ""))
            if match:
                item_id = match.group(1)
                page = urlsplit(page_url)
                menu_no = parse_qs(page.query).get("no", [""])[0]
                absolute = f"{page.scheme}://{page.netloc}{page.path}?no={menu_no}&pgMode=View&idx={item_id}"
                mode = "view"
        if mode != "view" or not item_id.isdigit():
            continue
        found.setdefault(item_id, {
            "source_id": item_id, "name": anchor.get_text(" ", strip=True), "url": absolute,
        })
    return list(found.values())


def analyze_site_html(
    *, site_key: str, requested_url: str, html: str,
    final_url: str | None = None, http_status: int = 200,
    adapter_name: str = "numeric_cms",
) -> ProbeResult:
    effective_url = final_url or requested_url
    soup = BeautifulSoup(html, "lxml")
    access = detect_access_block(final_url=effective_url, html=html)
    menus = numeric_menu_links(html, effective_url)
    query_menus = query_menu_links(html, effective_url)
    mcode_menus = mcode_menu_links(html, effective_url)
    legacy_php_links = sum(
        1 for anchor in soup.select("a[href]")
        if any(root in urlsplit(urljoin(effective_url, str(anchor.get("href") or ""))).path
               for root in ("/00main/", "/01about/", "/05piazza/"))
    )
    html_php_links = sum(
        1 for anchor in soup.select("a[href]")
        if re.fullmatch(r"/html/[^/]+/[^/]+\.php", urlsplit(urljoin(effective_url, str(anchor.get("href") or ""))).path,
                        re.IGNORECASE)
    )
    html_lower = html.lower()
    fingerprints = {
        "numeric_menu_links": len(menus),
        "query_menu_links": len(query_menus),
        "mcode_menu_links": len(mcode_menus),
        "legacy_php_links": legacy_php_links,
        "html_php_links": html_php_links,
        "board_list": bool(soup.select_one(".a_brdList")),
        "board_detail": bool(soup.select_one(".bdvTitle") or soup.select_one(".a_bdCont")),
        "content_container": has_content_container(soup),
        "bbs_id_token": bool(re.search(r"bbsid|bbs_id", html_lower)),
        "view_action": "action=view" in html_lower,
        "pknu_host": (urlsplit(effective_url).hostname or "").endswith("pknu.ac.kr"),
        "access_blocked": access.blocked,
        "access_block_reasons": list(access.reasons),
    }
    score = 0
    score += 4 if fingerprints["board_list"] else 0
    score += 3 if fingerprints["board_detail"] else 0
    score += 1 if fingerprints["content_container"] else 0
    score += 3 if fingerprints["numeric_menu_links"] >= 3 else (1 if menus else 0)
    if adapter_name in {"query_view_do", "query_view_legacy"}:
        score += 4 if fingerprints["query_menu_links"] >= 3 else (1 if query_menus else 0)
    score += 4 if fingerprints["mcode_menu_links"] >= 3 else (1 if mcode_menus else 0)
    score += 4 if fingerprints["legacy_php_links"] >= 3 else (1 if legacy_php_links else 0)
    score += 4 if fingerprints["html_php_links"] >= 3 else (1 if html_php_links else 0)
    score += 2 if fingerprints["bbs_id_token"] else 0
    score += 1 if fingerprints["view_action"] else 0
    score += 1 if fingerprints["pknu_host"] else 0
    confidence = min(1.0, round(score / 8, 2))
    status = "compatible" if score >= 5 else ("partial" if score >= 2 else "unsupported")
    warnings: list[str] = []
    if http_status >= 400:
        warnings.append(f"HTTP {http_status}")
        status = "unreachable"
    if access.blocked:
        status = "blocked"
        confidence = 1.0
        warnings.append(f"{ACCESS_BLOCKED}: {', '.join(access.reasons)}")
    supported_query_menus = query_menus if adapter_name in {"query_view_do", "query_view_legacy"} else []
    if (not access.blocked and not menus and not supported_query_menus and not mcode_menus
            and not legacy_php_links and not html_php_links):
        warnings.append("no supported CMS menu links found")
    return ProbeResult(
        site_key=site_key, requested_url=requested_url, final_url=effective_url,
        status=status, compatible=status == "compatible", confidence=confidence,
        http_status=http_status, fingerprints=fingerprints, warnings=warnings,
        error=({"code": ACCESS_BLOCKED, "message": "access denial page",
                "retryable": False} if access.blocked else None),
    )


def probe_site(
    *, site_key: str, url: str, session: requests.Session | None = None,
    timeout: int = 20, system_trust_fallback: bool = False,
    adapter_name: str = "numeric_cms",
) -> ProbeResult:
    client = session or requests.Session()
    try:
        response, fallback_used = get_with_tls_policy(
            client, url, timeout=timeout, allow_redirects=True,
            system_trust_fallback=system_trust_fallback,
        )
        response.encoding = "utf-8"
        response, _ = follow_meta_refresh_once(
            client, response, timeout=timeout,
            system_trust_fallback=system_trust_fallback,
        )
        result = analyze_site_html(
            site_key=site_key, requested_url=url, html=response.text,
            final_url=response.url, http_status=response.status_code,
            adapter_name=adapter_name,
        )
        if fallback_used:
            return ProbeResult(**{**result.to_dict(), "warnings": [*result.warnings, "system trust fallback used"]})
        return result
    except (requests.RequestException, ValueError) as exc:
        return ProbeResult(
            site_key=site_key, requested_url=url, final_url=None,
            status="unreachable" if isinstance(exc, requests.RequestException) else "unsupported",
            compatible=False, confidence=0.0, http_status=None,
            error={"code": classify_request_error(exc) if isinstance(exc, requests.RequestException) else "META_REFRESH_BLOCKED",
                   "message": str(exc), "retryable": isinstance(exc, requests.RequestException)},
        )

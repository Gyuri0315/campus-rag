"""Shared parser, downloader, and crawl runner for PKNU department CMS sites."""

import argparse
import hashlib
import json
import logging
import re
import ssl
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urljoin, urlparse

import requests
import urllib3
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.crawlers.common.schema import (  # noqa: E402
    CrawlStats,
    RunResult,
    apply_common_schema,
    attachment_error,
    build_attachment,
    document_slug,
    classify_document,
    log_run_result,
    normalize_attachment,
    now_kst,
    sanitize_attachment_filename,
    unique_attachment_filename,
    url_source_id,
)

# ─── 로깅 설정 ────────────────────────────────────────────────────────────────
from scripts.crawlers.common.logging import (  # noqa: E402
    configure_crawler_logging, log_attachment_events, log_event, set_run_id,
)
from scripts.crawlers.common.storage import (  # noqa: E402
    empty_state, get_dataset_paths, load_state_with_migration, save_state_atomic, write_document,
)
from scripts.crawlers.common.reader import remove_redundant_legacy_fields  # noqa: E402
from scripts.crawlers.departments.config import (  # noqa: E402
    DepartmentConfig, DEFAULT_REGISTRY_PATH, DEFAULT_SITE_CATALOG_PATH, audit_registry,
    load_site_catalog, validate_registry_catalog_links,
)
from scripts.crawlers.departments.probe import select_content_container  # noqa: E402
from scripts.crawlers.departments.adapters import get_adapter  # noqa: E402
from scripts.crawlers.departments.access import ACCESS_BLOCKED, detect_access_block  # noqa: E402
from scripts.crawlers.departments.urls import resolve_url, resolve_link
from scripts.crawlers.departments.tls import (  # noqa: E402
    NETWORK_REQUEST_FAILED,
    TLS_CERTIFICATE_VERIFY_FAILED,
    classify_request_error,
    get_with_tls_policy,
)

ACTIVE_CONFIG: DepartmentConfig | None = None
ACTIVE_ADAPTER = get_adapter("numeric_cms")
BASE_URL = ""
PATHS = None
OUTPUT_JSON = None
OUTPUT_HTML = None
OUTPUT_FILES = None
STATE_FILE = None
LEGACY_STATE_FILES: tuple[Path, ...] = ()
SECTIONS: list[dict[str, Any]] = []
REQUEST_DELAY = 0.8
LIST_DELAY = 0.5
REQUEST_TIMEOUT = 20
CRAWL_ALL_BOARD_PAGES = True
REUSE_EXISTING_ATTACHMENTS = True
INITIAL_MAX_PAGES = 10
INCREMENTAL_MAX_PAGES = 3
log = logging.getLogger("crawler.department")
log_context = None
TLS_SYSTEM_TRUST_FALLBACK_USED = False
_LAST_FETCH_FAILURE: "RequestFailure | None" = None


@dataclass(frozen=True)
class RequestFailure:
    """Structured request failure retained without changing fetch()'s public return type."""

    code: str
    url: str
    message: str
    retryable: bool


@dataclass
class CrawlDiagnostics:
    """Run-scoped details which do not belong in the stable CrawlStats schema."""

    sections_selected: int = 0
    sections_initialized: int = 0
    empty_sections: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)

    def add_failure(self, failure: RequestFailure, *, source_id: object = None) -> None:
        self.errors.append({
            "code": failure.code,
            "source_id": str(source_id) if source_id is not None else None,
            "url": failure.url,
            "message": failure.message,
            "retryable": failure.retryable,
        })


_RETRYABLE_HTTP_STATUSES = {408, 425, 429, 500, 502, 503, 504}


def _http_failure(response: requests.Response, requested_url: str) -> RequestFailure:
    status = int(response.status_code)
    return RequestFailure(
        code="HTTP_ERROR",
        url=response.url or requested_url,
        message=f"HTTP {status} while requesting {requested_url}",
        retryable=status in _RETRYABLE_HTTP_STATUSES,
    )


def _exception_failure(exc: requests.RequestException, url: str) -> RequestFailure:
    code = classify_request_error(exc)
    return RequestFailure(
        code=code,
        url=url,
        message=str(exc),
        retryable=code != TLS_CERTIFICATE_VERIFY_FAILED,
    )


def _record_request_failure(
    diagnostics: CrawlDiagnostics | None,
    failure: RequestFailure | None,
    *,
    url: str,
    source_id: object = None,
) -> None:
    if diagnostics is None:
        return
    if failure is None:
        failure = RequestFailure(
            code=NETWORK_REQUEST_FAILED,
            url=url,
            message="request failed without response details",
            retryable=True,
        )
    diagnostics.add_failure(failure, source_id=source_id)


def configure_department(config: DepartmentConfig) -> None:
    """Bind one validated department configuration to this engine process."""
    global ACTIVE_CONFIG, BASE_URL, PATHS, OUTPUT_JSON, OUTPUT_HTML, OUTPUT_FILES
    global STATE_FILE, LEGACY_STATE_FILES, SECTIONS, REQUEST_DELAY, LIST_DELAY
    global REQUEST_TIMEOUT, CRAWL_ALL_BOARD_PAGES, REUSE_EXISTING_ATTACHMENTS
    global INITIAL_MAX_PAGES, INCREMENTAL_MAX_PAGES, log, log_context, ACTIVE_ADAPTER
    global TLS_SYSTEM_TRUST_FALLBACK_USED

    ACTIVE_CONFIG = config
    ACTIVE_ADAPTER = get_adapter(config.adapter)
    BASE_URL = config.base_url
    PATHS = get_dataset_paths(PROJECT_ROOT, config.dataset)
    OUTPUT_JSON = PATHS.json
    OUTPUT_HTML = PATHS.html
    OUTPUT_FILES = PATHS.files
    STATE_FILE = PATHS.state
    LEGACY_STATE_FILES = tuple(PROJECT_ROOT / item for item in config.legacy_state_files)
    SECTIONS = [section.runtime_dict(config.base_url) for section in config.active_sections]
    REQUEST_DELAY = config.request_delay_seconds
    LIST_DELAY = config.list_delay_seconds
    REQUEST_TIMEOUT = config.request_timeout_seconds
    CRAWL_ALL_BOARD_PAGES = config.crawl_all_board_pages
    REUSE_EXISTING_ATTACHMENTS = config.reuse_existing_attachments
    INITIAL_MAX_PAGES = config.initial_max_pages
    INCREMENTAL_MAX_PAGES = config.incremental_max_pages
    log, log_context = configure_crawler_logging(config.dataset, PROJECT_ROOT)
    TLS_SYSTEM_TRUST_FALLBACK_USED = False

class _LegacySSLAdapter(HTTPAdapter):
    """
    SSLV3_ALERT_HANDSHAKE_FAILURE 등 레거시 SSL 핸드셰이크 오류를 우회하기 위한
    커스텀 어댑터. 낮은 보안 레벨의 사이퍼 허용 + 인증서 검증 비활성화.
    """

    def init_poolmanager(self, *args, **kwargs):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        # 레거시 사이퍼 스위트 허용 (SECLEVEL=1)
        ctx.set_ciphers("DEFAULT@SECLEVEL=1")
        # Python 3.12+ 에서 사용 가능한 레거시 재협상 허용 옵션
        if hasattr(ssl, "OP_LEGACY_SERVER_CONNECT"):
            ctx.options |= ssl.OP_LEGACY_SERVER_CONNECT
        kwargs["ssl_context"] = ctx
        super().init_poolmanager(*args, **kwargs)

    def proxy_manager_for(self, proxy, **proxy_kwargs):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        ctx.set_ciphers("DEFAULT@SECLEVEL=1")
        if hasattr(ssl, "OP_LEGACY_SERVER_CONNECT"):
            ctx.options |= ssl.OP_LEGACY_SERVER_CONNECT
        proxy_kwargs["ssl_context"] = ctx
        return super().proxy_manager_for(proxy, **proxy_kwargs)


def build_session() -> requests.Session:
    session = requests.Session()
    # OS/쉘의 잘못된 HTTP(S)_PROXY 설정이 있더라도 직접 접속하도록 고정
    session.trust_env = False
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8",
            "Referer": BASE_URL,
        }
    )
    return session


# ─── 상태 관리 (증분 크롤링) ──────────────────────────────────────────────────
def load_state() -> dict:
    state, origin = load_state_with_migration(PATHS, legacy_paths=LEGACY_STATE_FILES)
    log_event(log, logging.INFO, "state_loaded", path=STATE_FILE, entries=len(state["items"]), origin=origin)
    return state


def save_state(state: dict) -> None:
    if ACTIVE_CONFIG is None:
        raise RuntimeError("department crawler is not configured")
    save_state_atomic(STATE_FILE, state, ACTIVE_CONFIG.dataset)
    log_event(log, logging.INFO, "state_saved", path=STATE_FILE, entries=len(state["items"]))


# ─── 유틸리티 ─────────────────────────────────────────────────────────────────
def make_slug(url: str, extra: str = "") -> str:
    """URL + 추가 키로 고유 파일명 생성 (MD5 앞 12자)"""
    return hashlib.md5(f"{url}|{extra}".encode()).hexdigest()[:12]


def safe_text(el) -> str:
    return el.get_text(" ", strip=True) if el else ""


def ensure_dirs(category: str) -> tuple[Path, Path]:
    jd = PATHS.category_json(category)
    hd = PATHS.category_html(category)
    jd.mkdir(parents=True, exist_ok=True)
    hd.mkdir(parents=True, exist_ok=True)
    return jd, hd


def ensure_file_dir(category: str, slug: str) -> Path:
    fd = PATHS.attachment_dir(category, slug)
    fd.mkdir(parents=True, exist_ok=True)
    return fd


def save_document(doc: dict, raw_html: str) -> None:
    write_document(PATHS, doc, doc["category"], doc["slug"], raw_html)


def load_existing_attachments(category: str, slug: str) -> list[dict]:
    json_path = PATHS.document_json(category, slug)
    if not json_path.exists():
        return []

    try:
        doc = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []

    attachments = doc.get("attachments", [])
    if not isinstance(attachments, list):
        return []

    reusable: list[dict] = []
    for index, attachment in enumerate(attachments, start=1):
        if not isinstance(attachment, dict):
            continue

        saved_path = attachment.get("saved_path", "")
        if saved_path and (PROJECT_ROOT / saved_path).exists():
            reusable.append(normalize_attachment(attachment, index=index, project_root=PROJECT_ROOT))

    return reusable


# ─── HTTP 요청 ────────────────────────────────────────────────────────────────
def fetch(
    session: requests.Session,
    url: str,
    params: dict | None = None,
    delay: float = REQUEST_DELAY,
) -> requests.Response | None:
    global TLS_SYSTEM_TRUST_FALLBACK_USED, _LAST_FETCH_FAILURE
    _LAST_FETCH_FAILURE = None
    try:
        url = resolve_url(url)
    except ValueError as exc:
        _LAST_FETCH_FAILURE = RequestFailure(code=str(exc).split(':', 1)[0], url=str(url), message=str(exc), retryable=False)
        log_event(log, logging.WARNING, "url_skipped", error_code=_LAST_FETCH_FAILURE.code, reason=str(exc), retryable=False)
        return None
    time.sleep(delay)
    try:
        resp, fallback_used = get_with_tls_policy(
            session,
            url,
            params=params,
            timeout=REQUEST_TIMEOUT,
            system_trust_fallback=bool(
                ACTIVE_CONFIG and ACTIVE_CONFIG.tls_system_trust_fallback
            ),
        )
        if fallback_used:
            TLS_SYSTEM_TRUST_FALLBACK_USED = True
            log_event(
                log,
                logging.WARNING,
                "tls_fallback_used",
                url=url,
                policy="windows_system_ca",
            )
        resp.encoding = "utf-8"
        if resp.status_code == 200:
            blocked = detect_access_block(final_url=resp.url or url, html=resp.text)
            if blocked.blocked:
                _LAST_FETCH_FAILURE = RequestFailure(
                    code=ACCESS_BLOCKED,
                    url=resp.url or url,
                    message=f"access blocked ({', '.join(blocked.reasons)})",
                    retryable=False,
                )
                log_event(log, logging.ERROR, "request_failed", url=resp.url or url,
                          error_code=ACCESS_BLOCKED, retryable=False, requested_url=url,
                          reason="server_denial_response")
                return None
            return resp
        _LAST_FETCH_FAILURE = _http_failure(resp, url)
        log_event(log, logging.ERROR, "request_failed", url=_LAST_FETCH_FAILURE.url,
                  status_code=resp.status_code, error_code=_LAST_FETCH_FAILURE.code,
                  retryable=_LAST_FETCH_FAILURE.retryable)
    except requests.RequestException as exc:
        _LAST_FETCH_FAILURE = _exception_failure(exc, url)
        log_event(log, logging.ERROR, "request_failed", url=url, error=exc,
                  error_code=_LAST_FETCH_FAILURE.code, retryable=_LAST_FETCH_FAILURE.retryable)
    return None


# ─── 게시판 파싱 ──────────────────────────────────────────────────────────────
def sanitize_filename(filename: str) -> str:
    return sanitize_attachment_filename(filename)


def extract_filename(resp: requests.Response, url: str, fallback_name: str) -> str:
    content_disposition = resp.headers.get("Content-Disposition", "")
    if content_disposition:
        m = re.search(r"filename\*=UTF-8''([^;]+)", content_disposition, re.I)
        if m:
            return sanitize_filename(unquote(m.group(1)))
        m = re.search(r'filename="?([^";]+)"?', content_disposition, re.I)
        if m:
            return sanitize_filename(unquote(m.group(1)))

    parsed = urlparse(resp.url or url)
    basename = Path(parsed.path).name
    if basename:
        return sanitize_filename(unquote(basename))

    if fallback_name:
        return sanitize_filename(fallback_name)

    return "attachment"


def is_attachment_candidate(href: str, name: str = "") -> bool:
    href_l = href.lower()
    name_l = name.lower()

    blocked_exts = (".html", ".htm", ".shtml", ".php", ".asp", ".aspx", ".jsp")
    if href_l.endswith(blocked_exts) or name_l.endswith(blocked_exts):
        return False

    file_ext_pattern = (
        r"\.(pdf|hwp|hwpx|doc|docx|xls|xlsx|ppt|pptx|zip|rar|7z|txt|csv|png|jpg|jpeg|gif)$"
    )
    if re.search(file_ext_pattern, href_l) or re.search(file_ext_pattern, name_l):
        return True

    if any(k in href_l for k in ("download", "down", "attach", "file", "atchfile")):
        return True

    return False


def save_attachments(
    session: requests.Session,
    attachments: list[dict],
    category: str,
    slug: str,
    source_page_url: str,
) -> list[dict]:
    if not attachments:
        return []

    file_dir = ensure_file_dir(category, slug)
    results: list[dict] = []
    used_names: set[str] = set()

    for idx, attachment in enumerate(attachments, start=1):
        file_url = attachment.get("url", "").strip()
        if not file_url:
            results.append(
                build_attachment(
                    index=idx,
                    name=attachment.get("name") or f"attachment-{idx}",
                    url="",
                    project_root=PROJECT_ROOT,
                    error=attachment_error("MISSING_URL", "attachment URL is empty", False),
                    legacy={**attachment, "source_page_url": source_page_url, "source_site": BASE_URL},
                )
            )
            continue

        try:
            file_url = resolve_url(file_url, source_page_url)
        except ValueError as exc:
            results.append(build_attachment(index=idx, name=attachment.get("name") or f"attachment-{idx}",
                url=file_url, project_root=PROJECT_ROOT,
                error=attachment_error(str(exc).split(':', 1)[0], str(exc), False)))
            continue

        try:
            time.sleep(0.2)
            resp, fallback_used = get_with_tls_policy(
                session,
                file_url,
                timeout=REQUEST_TIMEOUT,
                stream=True,
                headers={"Referer": source_page_url or BASE_URL},
                system_trust_fallback=bool(
                    ACTIVE_CONFIG and ACTIVE_CONFIG.tls_system_trust_fallback
                ),
            )
            if fallback_used:
                log_event(
                    log, logging.WARNING, "tls_fallback_used",
                    url=file_url, policy="windows_system_ca",
                )
        except requests.RequestException as exc:
            log.warning("첨부파일 다운로드 실패 %s: %s", file_url, exc)
            results.append(
                build_attachment(
                    index=idx,
                    name=attachment.get("name") or f"attachment-{idx}",
                    url=file_url,
                    project_root=PROJECT_ROOT,
                    error=attachment_error("REQUEST_FAILED", exc, True),
                    legacy={**attachment, "source_page_url": source_page_url, "source_site": BASE_URL},
                )
            )
            continue

        final_url = resp.url
        content_type = (resp.headers.get("Content-Type", "") or "").lower()
        if resp.status_code != 200:
            log.warning("첨부파일 HTTP %d %s", resp.status_code, file_url)
            resp.close()
            results.append(
                build_attachment(
                    index=idx,
                    name=attachment.get("name") or f"attachment-{idx}",
                    url=file_url,
                    final_url=final_url,
                    project_root=PROJECT_ROOT,
                    content_type=content_type,
                    error=attachment_error(
                        f"HTTP_{resp.status_code}",
                        f"attachment returned HTTP {resp.status_code}",
                        resp.status_code == 429 or resp.status_code >= 500,
                    ),
                    legacy={**attachment, "source_page_url": source_page_url, "source_site": BASE_URL},
                )
            )
            continue

        filename = extract_filename(resp, file_url, attachment.get("name", ""))
        if "text/html" in content_type or "application/xhtml+xml" in content_type:
            log.info("HTML 응답은 첨부로 저장하지 않음: %s", file_url)
            resp.close()
            results.append(
                build_attachment(
                    index=idx,
                    name=filename,
                    url=file_url,
                    final_url=final_url,
                    project_root=PROJECT_ROOT,
                    content_type=content_type,
                    error=attachment_error("UNSUPPORTED_CONTENT_TYPE", content_type, False),
                    legacy={**attachment, "source_page_url": source_page_url, "source_site": BASE_URL},
                )
            )
            continue

        if filename.lower().endswith((".html", ".htm", ".shtml")):
            log.info("HTML 파일명은 첨부로 저장하지 않음: %s", filename)
            resp.close()
            results.append(
                build_attachment(
                    index=idx,
                    name=filename,
                    url=file_url,
                    final_url=final_url,
                    project_root=PROJECT_ROOT,
                    content_type=content_type,
                    error=attachment_error("UNSUPPORTED_FILE_TYPE", Path(filename).suffix, False),
                    legacy={**attachment, "source_page_url": source_page_url, "source_site": BASE_URL},
                )
            )
            continue

        if "." not in filename:
            fallback_ext = Path(urlparse(resp.url).path).suffix
            if fallback_ext:
                filename = f"{filename}{fallback_ext}"

        filename = unique_attachment_filename(filename, used_names)

        output_file = file_dir / filename
        try:
            with output_file.open("wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
            saved_path = output_file
            downloaded = True
            save_error = None
        except (OSError, requests.RequestException) as exc:
            log.warning("첨부파일 저장 실패 %s: %s", output_file, exc)
            saved_path = None
            downloaded = False
            error_code = "STREAM_FAILED" if isinstance(exc, requests.RequestException) else "SAVE_FAILED"
            save_error = attachment_error(error_code, exc, True)
        finally:
            resp.close()

        results.append(
            build_attachment(
                index=idx,
                name=filename,
                url=file_url,
                final_url=final_url,
                saved_path=saved_path,
                downloaded=downloaded,
                project_root=PROJECT_ROOT,
                content_type=content_type,
                error=save_error,
                legacy={**attachment, "source_page_url": source_page_url, "source_site": BASE_URL},
            )
        )

    return results


def attachment_metadata_only(attachments: list[dict], source_page_url: str) -> list[dict]:
    """Normalize discovered attachments without issuing download requests."""
    return [
        build_attachment(
            index=index,
            name=attachment.get("name") or f"attachment-{index}",
            url=str(attachment.get("url") or ""),
            project_root=PROJECT_ROOT,
            legacy={**attachment, "source_page_url": source_page_url, "source_site": BASE_URL},
        )
        for index, attachment in enumerate(attachments or [], start=1)
    ]


def parse_list_page(soup: BeautifulSoup, board_url: str) -> list[dict]:
    """게시판 목록에서 게시글 정보(번호·제목·날짜·URL) 추출"""
    return ACTIVE_ADAPTER.parse_list(soup, board_url)


def _legacy_parse_list_page(soup: BeautifulSoup, board_url: str) -> list[dict]:
    """Retained temporarily as a readable reference for the golden parser contract."""
    items: list[dict] = []
    for row in soup.select(".a_brdList tr"):
        tds = row.select("td")
        link_el = row.select_one("td a[href]")
        if not tds or not link_el:
            continue

        num_text = tds[0].get_text(strip=True)
        is_notice = num_text.upper() == "NOTICE"
        post_no: int | None = None
        if not is_notice:
            try:
                post_no = int(num_text)
            except ValueError:
                pass

        href = link_el.get("href", "")
        post_url = urljoin(board_url, href)
        date_text = tds[-2].get_text(strip=True) if len(tds) >= 2 else ""

        items.append(
            {
                "post_url": post_url,
                "num": num_text,
                "post_no": post_no,       # 정수 번호 (고정글은 None)
                "is_notice": is_notice,
                "date": date_text,
            }
        )
    return items


_NAV_TEXTS = {"목록보기", "다음", "이전", "next", "prev"}

# 텍스트 후처리에서 제거할 네비게이션 문구 패턴
_NAV_PHRASE_RE = re.compile(
    r"(다음\s*게시글이\s*없습니다\.?|이전\s*게시글이\s*없습니다\.?)",
    re.IGNORECASE,
)


def _is_nav_only_token(token: str) -> bool:
    """토큰이 네비게이션 단어만으로 구성되어 있으면 True"""
    words = token.lower().split()
    return bool(words) and all(w in _NAV_TEXTS for w in words)


def extract_body_content(content_el) -> str:
    """
    .a_bdCont 요소에서 실제 본문(.bdvEdit)만 추출한다.
    - .c_bdvBtn (목록보기), .c_bdvNav (이전글/다음글) 제거 후
    - .bdvEdit 영역이 있으면 해당 텍스트만 사용
    - 없으면 메타 테이블 제거 후 전체 텍스트로 폴백
    """
    from scripts.crawlers.departments.adapters.numeric_cms import extract_body_content as extract
    return extract(content_el)


def _legacy_extract_body_content(content_el) -> str:
    if content_el is None:
        return ""

    soup_copy = BeautifulSoup(str(content_el), "lxml")

    # 목록보기 버튼 영역 제거
    for el in soup_copy.select(".c_bdvBtn"):
        el.decompose()

    # 이전글/다음글 네비게이션 테이블 제거
    for el in soup_copy.select(".c_bdvNav"):
        el.decompose()

    # 기타 네비게이션 관련 클래스 요소 제거
    for nav_sel in (".a_bdPaging", ".board-nav", ".btn-list"):
        for el in soup_copy.select(nav_sel):
            el.decompose()

    # 실제 본문 영역(.bdvEdit)이 있으면 해당 텍스트만 추출
    body_el = soup_copy.select_one(".bdvEdit")
    if body_el:
        text = body_el.get_text(" ", strip=True)
    else:
        # 폴백: 첫 번째 메타 테이블 제거 후 전체 텍스트 사용
        first_table = soup_copy.select_one("table")
        if first_table:
            first_table.decompose()
        # 남은 네비게이션 텍스트 a 태그 제거
        for a in soup_copy.select("a"):
            if a.get_text(strip=True).lower() in _NAV_TEXTS:
                a.decompose()
        text = soup_copy.get_text(" ", strip=True)

    # 고정 네비게이션 문구 제거
    text = _NAV_PHRASE_RE.sub("", text)

    # 연속 공백·개행 정리
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()


def parse_view_page(soup: BeautifulSoup, post_url: str, item: dict) -> dict | None:
    """게시글 상세 페이지에서 문서 필드 추출"""
    return ACTIVE_ADAPTER.parse_detail(
        soup, post_url, item, base_url=BASE_URL,
        site_prefix=ACTIVE_CONFIG.site_prefix if ACTIVE_CONFIG else "",
    )


def _legacy_parse_view_page(soup: BeautifulSoup, post_url: str, item: dict) -> dict | None:
    """Retained temporarily as a readable reference for the golden parser contract."""
    title_el = soup.select_one(".bdvTitle")
    content_el = soup.select_one(".a_bdCont")

    if not title_el and not content_el:
        return None

    title = safe_text(title_el)
    body = extract_body_content(content_el)

    # 날짜: 본문 메타 테이블에서 추출 시도
    date = item.get("date", "")
    if content_el and not date:
        m = re.search(r"\d{4}-\d{2}-\d{2}", safe_text(content_el))
        if m:
            date = m.group()

    # 첨부파일
    attachments: list[dict] = []
    if content_el:
        for a in content_el.select("a[href]"):
            href = a.get("href", "")
            name = a.get_text(strip=True)
            if not href or href.startswith("javascript"):
                continue

            # 네비게이션 텍스트 링크 제외 ("목록보기", "다음", "이전" 등)
            if name.lower() in _NAV_TEXTS:
                continue

            abs_url = urljoin(BASE_URL, href)
            parsed_href = urlparse(abs_url)

            # action=view 파라미터가 포함된 게시판 뷰 링크 제외
            if "action=view" in parsed_href.query:
                continue

            # 같은 학과 사이트의 숫자 메뉴 링크는 첨부파일이 아니다.
            base = urlparse(BASE_URL)
            menu_path = rf"/{re.escape(ACTIVE_CONFIG.site_prefix)}/\d+" if ACTIVE_CONFIG else r"/$^"
            if parsed_href.netloc == base.netloc and re.fullmatch(menu_path, parsed_href.path):
                continue

            if is_attachment_candidate(href, name):
                attachments.append({"name": name, "url": abs_url})

    return {
        "slug": make_slug(post_url, title),
        "title": title,
        "date": date,
        "url": post_url,
        "is_notice": item.get("is_notice", False),
        "body": body,
        "attachments": attachments,
    }


# ─── 게시판 크롤러 ────────────────────────────────────────────────────────────
def crawl_board(
    session: requests.Session,
    section: dict,
    state: dict,
    is_initial: bool,
    recent_only: int | None = None,
    max_items: int | None = None,
    no_download_files: bool = False,
    diagnostics: CrawlDiagnostics | None = None,
    retry_items: list[dict] | None = None,
) -> tuple[CrawlStats, int]:
    """
    게시판을 크롤링한다.
    - 증분 모드: state에 저장된 마지막 게시글 번호 이후의 새 게시글만 수집
    - 초기 모드: INITIAL_MAX_PAGES 페이지까지 수집
    반환: (저장 건수, 최고 게시글 번호)
    """
    board_url = section["url"]
    bbs_id = section.get("bbs_id", "")
    category = section["category"]
    name = section["name"]
    doc_type = section["type"]

    state_key = board_url
    numeric_incremental = ACTIVE_ADAPTER.uses_numeric_post_order
    stored_last_no: int = state.setdefault("items", {}).get(state_key, {}).get("last_no", 0)
    if recent_only is not None:
        last_known_no = stored_last_no
        max_pages = recent_only
    else:
        last_known_no = 0 if CRAWL_ALL_BOARD_PAGES else stored_last_no
        max_pages = None if CRAWL_ALL_BOARD_PAGES else (
            INITIAL_MAX_PAGES if is_initial else INCREMENTAL_MAX_PAGES
        )

    log.info(
        "[게시판] %s | last_no=%d | max_pages=%s",
        name, last_known_no, "ALL" if max_pages is None else str(max_pages)
    )

    stats = CrawlStats()
    if retry_items is not None:
        last_known_no, max_pages = -1, 1
    new_max_no = stored_last_no
    seen_post_urls: set[str] = set()

    page = 1
    while max_pages is None or page <= max_pages:
        list_url, params = ACTIVE_ADAPTER.list_request(board_url, page, bbs_id or None)
        resp = fetch(session, list_url, params=params, delay=LIST_DELAY) if retry_items is None else None
        if resp is None and retry_items is None:
            stats.failed += 1
            _record_request_failure(
                diagnostics, _LAST_FETCH_FAILURE, url=list_url, source_id=section.get("id") or name,
            )
            break
        if page == 1 and diagnostics is not None:
            diagnostics.sections_initialized += 1

        soup = BeautifulSoup(resp.text if resp is not None else "", "lxml")
        effective_list_url = (getattr(resp, "url", None) or board_url) if resp is not None else board_url
        items = parse_list_page(soup, effective_list_url) if retry_items is None else retry_items
        stats.discovered += len(items)
        log_event(log, logging.INFO, "list_fetched", section=name, page=page, discovered=len(items), url=board_url)
        if not items:
            candidate_count = ACTIVE_ADAPTER.count_list_candidates(soup, board_url)
            if candidate_count:
                stats.failed += 1
                message = (
                    f"adapter {ACTIVE_ADAPTER.name} found {candidate_count} detail candidate(s), "
                    "but parsed no documents"
                )
                if diagnostics is not None:
                    diagnostics.errors.append({
                        "code": "PARSER_MISMATCH",
                        "source_id": str(section.get("id") or name),
                        "url": board_url,
                        "message": message,
                        "retryable": False,
                    })
                log_event(
                    log, logging.ERROR, "document_failed",
                    source_id=section.get("id") or name, url=board_url,
                    stage="list_parse", reason="parser_mismatch",
                    candidates=candidate_count,
                )
            else:
                if diagnostics is not None:
                    diagnostics.empty_sections += 1
                log_event(
                    log, logging.INFO, "list_fetched", section=name, page=page,
                    discovered=0, candidates=0, status="empty", url=board_url,
                )
                log.info("  p%d: 정상 빈 목록 → 종료", page)
            break

        # Some legacy boards expose a page made entirely of pinned notices.
        # Those are still valid documents and must be processed.
        processable = [
            item for item in items
            if str(item.get("post_url") or "").strip()
            and (numeric_incremental or str(item.get("source_id") or "").strip())
        ]
        if not processable:
            stats.skipped += len(items)
            stats.failed += 1
            message = (
                f"adapter {ACTIVE_ADAPTER.name} discovered {len(items)} item(s), "
                "but none had a usable post_url/source_id"
            )
            if diagnostics is not None:
                diagnostics.errors.append({
                    "code": "PARSER_MISMATCH",
                    "source_id": str(section.get("id") or name),
                    "url": board_url,
                    "message": message,
                    "retryable": False,
                })
            log_event(
                log, logging.ERROR, "document_failed",
                source_id=section.get("id") or name, url=board_url,
                stage="list_parse", reason="parser_mismatch",
                discovered=len(items),
            )
            break

        if page > 1 and all(item["post_url"] in seen_post_urls for item in processable):
            log.info("  p%d: 이전 페이지와 동일한 게시글 → 종료", page)
            break

        numbered = [it for it in processable if it.get("post_no") is not None]
        page_min_no: int | None = None
        page_max_no: int | None = None
        if numeric_incremental and numbered:
            regular = [it for it in numbered if not it["is_notice"]]
            range_items = regular or numbered
            page_max_no = max(int(it["post_no"]) for it in range_items)
            page_min_no = min(int(it["post_no"]) for it in range_items)
            log.info("  p%d: %d개 (no %d~%d)", page, len(processable), page_min_no, page_max_no)
        else:
            log.info("  p%d: %d개 (source_id 기반)", page, len(processable))

        # 이미 수집한 게시글만 있으면 중단
        if page_max_no is not None and page_max_no <= last_known_no:
            log.info("  p%d: 모두 기수집 → 종료 (last_no=%d)", page, last_known_no)
            break

        if page_max_no is not None:
            new_max_no = max(new_max_no, page_max_no)

        # 신규 게시글만 상세 크롤링
        for item in processable:
            if max_items is not None and stats.requested >= max_items:
                return stats, stored_last_no
            native_source_id = str(item.get("source_id") or "").strip()
            post_no = item.get("post_no")
            item_token = native_source_id or (
                str(post_no) if post_no is not None else url_source_id(item["post_url"])
            )
            source_id = (
                f"{bbs_id or url_source_id(board_url)}:{item_token}"
                if numeric_incremental else native_source_id
            )
            if retry_items is not None:
                source_id = item["retry_source_id"]
            log_event(log, logging.DEBUG, "document_discovered", source_id=source_id, url=item.get("post_url"))
            if item["post_url"] in seen_post_urls:
                stats.skipped += 1
                log_event(log, logging.INFO, "document_skipped", source_id=source_id, url=item["post_url"], reason="duplicate")
                continue
            seen_post_urls.add(item["post_url"])

            # 고정글(NOTICE) 포함 수집
            if numeric_incremental and post_no is not None and int(post_no) <= last_known_no:
                stats.skipped += 1
                log_event(log, logging.INFO, "document_skipped", source_id=post_no, url=item["post_url"], reason="state")
                continue  # 이미 수집함

            stats.requested += 1
            post_resp = fetch(session, item["post_url"])
            if post_resp is None:
                stats.failed += 1
                _record_request_failure(
                    diagnostics, _LAST_FETCH_FAILURE, url=item["post_url"], source_id=source_id,
                )
                log_event(log, logging.ERROR, "document_failed", source_id=source_id, url=item["post_url"], stage="request")
                continue

            try:
                post_soup = BeautifulSoup(post_resp.text, "lxml")
                effective_url = getattr(post_resp, "url", None) or item["post_url"]
                view = parse_view_page(post_soup, effective_url, item)
                if view is None:
                    stats.failed += 1
                    if diagnostics is not None:
                        diagnostics.errors.append({
                            "code": "DOCUMENT_PARSE_FAILED",
                            "source_id": source_id,
                            "url": item["post_url"],
                            "message": "document parser returned no result",
                            "retryable": False,
                        })
                    log_event(log, logging.ERROR, "document_failed", source_id=source_id, url=item["post_url"], stage="parse")
                    continue

                view["slug"] = document_slug(ACTIVE_CONFIG.dataset, source_id)

                existing_attachments = (
                    load_existing_attachments(category, view["slug"])
                    if REUSE_EXISTING_ATTACHMENTS and not no_download_files
                    else []
                )
                if existing_attachments:
                    view["attachments"] = existing_attachments
                elif no_download_files:
                    view["attachments"] = attachment_metadata_only(
                        view.get("attachments", []), item["post_url"]
                    )
                else:
                    view["attachments"] = save_attachments(
                        session=session,
                        attachments=view.get("attachments", []),
                        category=category,
                        slug=view["slug"],
                        source_page_url=item["post_url"],
                    )

                doc = {
                    **view,
                    "category": category,
                    "subcategory": name,
                    "type": doc_type,
                    "content": view.pop("body"),
                    "crawled_at": datetime.now().isoformat(),
                }
                doc = apply_common_schema(
                    doc,
                    source_dataset=ACTIVE_CONFIG.dataset,
                    source_id=source_id,
                    source_site=BASE_URL,
                    document_type="notice",
                    content_source="pknu_cms_html",
                    author=doc.get("author"),
                    published_at=doc.get("date"),
                    metadata={
                        **({"requested_url": item["post_url"]} if effective_url != item["post_url"] else {}),
                        "bbs_id": bbs_id or None,
                        "post_no": item.get("post_no"),
                        "is_notice": item.get("is_notice", False),
                        "legacy_type": doc_type,
                        **({"source_type": section["source_type"]} if section.get("source_type") else {}),
                    },
                    crawled_at=doc.get("crawled_at"),
                )
                remove_redundant_legacy_fields(doc)
                if validate_body_content(doc, view, diagnostics):
                    stats.failed += 1
                    save_document(doc, post_resp.text)
                    continue
                existing_path = PATHS.document_json(category, doc["slug"])
                existing_hash = ""
                if existing_path.exists():
                    try:
                        existing_hash = json.loads(existing_path.read_text(encoding="utf-8")).get("content_hash", "")
                    except (OSError, json.JSONDecodeError):
                        pass
                outcome = classify_document(existing_hash, doc.get("content_hash"))
                setattr(stats, outcome, getattr(stats, outcome) + 1)
                stats.count_attachments(doc.get("attachments"))
                log_attachment_events(log, doc.get("attachments"), source_id=source_id)
                save_document(doc, post_resp.text)
                event = "document_unchanged" if outcome == "unchanged" else "document_saved"
                log_event(log, logging.INFO, event, source_id=source_id, url=doc.get("url"), status=outcome)
            except Exception as exc:
                stats.failed += 1
                if diagnostics is not None:
                    diagnostics.errors.append({"code": "DOCUMENT_PROCESSING_FAILED", "source_id": source_id,
                                               "url": item["post_url"], "message": str(exc), "retryable": False})
                log_event(log, logging.ERROR, "document_failed", source_id=source_id,
                          url=item["post_url"], stage="processing", error=str(exc), retryable=False)
                continue

        # 이번 페이지에 last_known_no 이하의 번호가 포함됐으면 다음 페이지는 불필요
        if numeric_incremental and page_min_no is not None and page_min_no <= last_known_no:
            log.info("  p%d: 일부 기수집 → 다음 페이지 불필요", page)
            break
        if (
            numeric_incremental
            and not numbered
            and not any(str(item.get("source_id") or "").strip() for item in processable)
        ):
            # Pinned-only pages are processable, but have no safe numeric
            # pagination boundary. Newer variants provide stable source_id
            # values and are protected by the repeated-page check above.
            break

        page += 1

    log.info("[게시판] %s 완료 → 신규 %d건 | new_max_no=%d", name, stats.new, new_max_no)
    # Never advance the watermark past a failed request/parser result.
    return stats, stored_last_no if stats.failed else new_max_no


# ─── 정적 페이지 크롤러 ───────────────────────────────────────────────────────
def validate_body_content(doc, parsed, diagnostics=None):
    """Record extraction gaps instead of silently treating empty text as success."""
    if str(doc.get("content") or "").strip():
        return False
    state = parsed.get("content_state") or ("attachment_only" if doc.get("attachments") else "empty")
    doc["metadata"]["content_state"] = state
    if parsed.get("content_images"):
        doc["metadata"]["content_images"] = [urljoin(doc["url"], src) for src in parsed["content_images"]]
    code = {"attachment_only": "ATTACHMENT_ONLY", "image_only": "IMAGE_ONLY_REQUIRES_OCR"}.get(state, "EMPTY_BODY")
    doc["crawl"]["warnings"].append(code)
    doc["crawl"]["status"] = "success" if state == "attachment_only" else "partial_success" if state == "image_only" else "failed"
    log_event(log, logging.WARNING, "content_validation", source_id=doc["source_id"], url=doc["url"], code=code)
    if state != "attachment_only" and diagnostics is not None:
        diagnostics.errors.append({"code": code, "source_id": doc["source_id"], "url": doc["url"],
                                   "message": "No extractable body text", "retryable": False})
    return state == "empty"


def crawl_static(
    session: requests.Session,
    section: dict,
    diagnostics: CrawlDiagnostics | None = None,
) -> CrawlStats:
    """정적 소개/안내 페이지를 크롤링해 저장한다."""
    page_url = section["url"]
    category = section["category"]
    name = section["name"]
    doc_type = section["type"]

    log.info("[정적] %s (%s)", name, page_url)
    resp = fetch(session, page_url, delay=LIST_DELAY)
    if resp is None:
        _record_request_failure(
            diagnostics, _LAST_FETCH_FAILURE, url=page_url, source_id=section.get("id") or name,
        )
        return CrawlStats(discovered=1, requested=1, failed=1)
    if diagnostics is not None:
        diagnostics.sections_initialized += 1

    soup = BeautifulSoup(resp.text, "lxml")

    # 제목: breadcrumb 마지막 항목 또는 <title>
    parsed = ACTIVE_ADAPTER.parse_static(soup, fallback_title=name)
    title = parsed["title"]
    content_text = parsed["content"]
    for attachment in parsed.get("attachments", []):
        attachment["url"] = urljoin(page_url, attachment["url"])

    source_id = f"static:{url_source_id(page_url)}"
    slug = document_slug(ACTIVE_CONFIG.dataset, source_id)
    doc = {
        "slug": slug,
        "title": title,
        "date": "",
        "url": page_url,
        "category": category,
        "subcategory": name,
        "type": doc_type,
        "is_notice": False,
        "content": content_text,
        "attachments": parsed.get("attachments", []),
        "crawled_at": datetime.now().isoformat(),
    }
    doc = apply_common_schema(
        doc,
        source_dataset=ACTIVE_CONFIG.dataset,
        source_id=source_id,
        source_site=BASE_URL,
        document_type="static_page",
        content_source="pknu_cms_html",
        metadata={
            "section_name": name,
            "legacy_type": doc_type,
            **({"source_type": section["source_type"]} if section.get("source_type") else {}),
        },
        crawled_at=doc.get("crawled_at"),
    )
    remove_redundant_legacy_fields(doc)
    stats = CrawlStats(discovered=1, requested=1)
    if validate_body_content(doc, parsed, diagnostics):
        stats.failed = 1
        save_document(doc, resp.text)
        return stats
    existing_path = PATHS.document_json(category, doc["slug"])
    existing_hash = ""
    if existing_path.exists():
        try:
            existing_hash = json.loads(existing_path.read_text(encoding="utf-8")).get("content_hash", "")
        except (OSError, json.JSONDecodeError):
            pass
    outcome = classify_document(existing_hash, doc.get("content_hash"))
    setattr(stats, outcome, 1)
    save_document(doc, resp.text)
    log.info("[정적] %s 완료", name)
    return stats


# ─── 전체 크롤링 실행 ─────────────────────────────────────────────────────────
def run_crawl(
    recent_only: int | None = None,
    section_id: str | None = None,
    max_board_sections: int | None = None,
    max_items: int | None = None,
    no_download_files: bool = False,
    diagnostics: CrawlDiagnostics | None = None,
) -> CrawlStats:
    start = datetime.now()
    log.info("=" * 60)
    log.info("크롤링 시작: %s", start.strftime("%Y-%m-%d %H:%M:%S"))

    state = load_state()
    is_initial = not bool(state.get("items"))  # state가 비어 있으면 최초 실행
    if is_initial:
        log.info("최초 실행: 게시판당 최대 %d페이지 수집", INITIAL_MAX_PAGES)
    else:
        log.info("증분 실행: 신규 게시글만 수집 (최대 %d페이지)", INCREMENTAL_MAX_PAGES)
    if recent_only is not None:
        log.info("최근 수집 모드: 게시판당 최근 %d페이지 목록만 확인", recent_only)
    log.info("=" * 60)

    session = build_session()
    total_stats = CrawlStats()
    new_state = dict(state)

    sections = [section for section in SECTIONS if section_id is None or section["id"] == section_id]
    if max_board_sections is not None:
        # A board-limited smoke run intentionally excludes static pages.
        sections = [section for section in sections if section["is_board"]][:max_board_sections]
    if diagnostics is not None:
        diagnostics.sections_selected = len(sections)
    remaining = max_items
    for section in sections:
        if remaining is not None and remaining <= 0:
            break
        log_event(log, logging.INFO, "section_started", section=section["name"], url=section["url"])
        try:
            if section["is_board"]:
                section_stats, new_max_no = crawl_board(
                    session,
                    section,
                    state,
                    is_initial,
                    recent_only=recent_only,
                    max_items=remaining,
                    no_download_files=no_download_files,
                    diagnostics=diagnostics,
                )
                total_stats.add(section_stats)
                # 상태 갱신 (max_no 증가 시에만)
                key = section["url"]
                prev_no = state.setdefault("items", {}).get(key, {}).get("last_no", 0)
                if (
                    ACTIVE_ADAPTER.uses_numeric_post_order
                    and max_items is None
                    and new_max_no > prev_no
                ):
                    new_state.setdefault("items", {})[key] = {
                        "source_id": key,
                        "slug": "",
                        "content_hash": "",
                        "last_seen_at": now_kst(),
                        "miss_count": 0,
                        "status": "active",
                        "last_no": new_max_no,
                        "name": section["name"],
                        "updated_at": now_kst(),
                    }
            else:
                section_stats = crawl_static(session, section, diagnostics=diagnostics)
                total_stats.add(section_stats)
            if remaining is not None:
                remaining -= section_stats.requested
        except Exception as exc:
            total_stats.failed += 1
            error_code = "OUTPUT_PATH_ERROR" if isinstance(exc, OSError) else "SECTION_PROCESSING_FAILED"
            if diagnostics is not None:
                diagnostics.errors.append({
                    "code": error_code,
                    "source_id": str(section.get("id") or section["name"]),
                    "url": section["url"],
                    "message": str(exc),
                    "retryable": False,
                })
            log.error("섹션 오류 [%s]: %s", section["name"], exc, exc_info=True)
            log_event(log, logging.ERROR, "document_failed", source_id=section["name"], url=section["url"], exc_info=True)
        finally:
            log_event(log, logging.INFO, "section_finished", section=section["name"])

    save_state(new_state)
    elapsed = (datetime.now() - start).total_seconds()
    log.info("=" * 60)
    log.info(
        "크롤링 완료: 총 %d건 저장 | 소요 %.1f초 | state 저장됨",
        total_stats.new + total_stats.updated, elapsed,
    )
    log.info("=" * 60)
    return total_stats




# ─── 진입점 ───────────────────────────────────────────────────────────────────
def finish_run_result(result: RunResult, diagnostics: CrawlDiagnostics) -> RunResult:
    """Apply department run semantics after every selected section was attempted."""
    if diagnostics.sections_selected > 0 and diagnostics.sections_initialized == 0:
        return result.finish("failed")
    if (
        not result.errors
        and diagnostics.empty_sections
        and result.stats.requested == 0
    ):
        result.add_error(
            "NO_DOCUMENTS_VERIFIED",
            f"{diagnostics.empty_sections} section(s) returned a normal empty list; no document was verified",
            retryable=False,
        )
    if result.stats.failed or result.stats.attachments_failed or result.errors:
        return result.finish("partial_success")
    return result.finish()


def _run_config(config: DepartmentConfig, args: argparse.Namespace) -> int:
    smoke = bool(
        args.section or args.max_board_sections is not None
        or args.max_items is not None or args.no_download_files
    )
    mode = "smoke" if smoke else ("recent" if args.recent_only is not None else "incremental")
    result = RunResult(dataset=config.dataset, mode=mode)
    try:
        configure_department(config)
    except Exception as exc:
        result.add_error("CONFIGURATION_ERROR", exc, retryable=False)
        result.finish("failed")
        try:
            result.save(PROJECT_ROOT)
        except OSError:
            pass
        logging.getLogger("crawler.department").critical(
            "department crawler configuration failed: %s", exc, exc_info=True,
        )
        return result.exit_code
    set_run_id(log_context, result.run_id)
    log_event(log, logging.INFO, "run_started", mode=mode)
    diagnostics = CrawlDiagnostics()
    try:
        if args.reset_state:
            save_state_atomic(STATE_FILE, empty_state(config.dataset), config.dataset)
            log_event(log, logging.INFO, "state_saved", path=STATE_FILE, action="reset")
        result.stats = run_crawl(
            recent_only=args.recent_only,
            section_id=args.section,
            max_board_sections=args.max_board_sections,
            max_items=args.max_items,
            no_download_files=args.no_download_files,
            diagnostics=diagnostics,
        )
        for error in diagnostics.errors:
            result.add_error(**error)
        if result.stats.failed and not diagnostics.errors:
            result.add_error("DOCUMENT_FAILURES", f"{result.stats.failed} document(s) failed", retryable=False)
        if result.stats.attachments_failed:
            result.add_error("ATTACHMENT_FAILURES", f"{result.stats.attachments_failed} attachment(s) failed", retryable=False)
        finish_run_result(result, diagnostics)
    except KeyboardInterrupt:
        result.finish("cancelled")
    except Exception as exc:
        code = "OUTPUT_PATH_ERROR" if isinstance(exc, OSError) else "RUN_INITIALIZATION_FAILED"
        result.add_error(code, exc, retryable=False)
        result.finish("failed")
        log_event(log, logging.CRITICAL, "run_finished", status="failed", exc_info=True)
    try:
        result_path = result.save(PROJECT_ROOT)
    except OSError as exc:
        result.add_error("OUTPUT_PATH_ERROR", exc, retryable=False)
        result.finish("failed")
        log_event(log, logging.CRITICAL, "run_finished", status="failed", error_code="OUTPUT_PATH_ERROR")
        log_run_result(log, result)
        return result.exit_code
    if result.status != "failed":
        log_event(log, logging.INFO, "run_finished", status=result.status, stats=result.stats.to_dict(), result_path=result_path)
    log_run_result(log, result, result_path)
    return result.exit_code


def crawl_ready_configs(registry: dict[str, DepartmentConfig]) -> list[DepartmentConfig]:
    """Return enabled, configured datasets in stable registry order."""
    return [config for config in registry.values() if config.crawl_ready]


def main(default_dataset: str | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="부경대학교 학과 홈페이지 공통 CMS 크롤러"
    )
    parser.add_argument(
        "--dataset",
        default=default_dataset,
        help="department dataset defined in the registry",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="registry의 활성화되고 section이 준비된 모든 학과를 순차 실행",
    )
    parser.add_argument(
        "--registry",
        type=Path,
        default=DEFAULT_REGISTRY_PATH,
        help="department registry JSON path",
    )
    parser.add_argument(
        "--sites",
        type=Path,
        default=DEFAULT_SITE_CATALOG_PATH,
        help="department homepage catalog CSV path",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="스케줄링 없이 즉시 1회만 실행",
    )
    parser.add_argument(
        "--reset-state",
        action="store_true",
        help="선택한 데이터셋의 상태를 초기화 후 다시 수집",
    )
    parser.add_argument(
        "--recent-only",
        type=int,
        metavar="N",
        default=None,
        help="게시판별 최근 N페이지만 목록 수집",
    )
    parser.add_argument(
        "--section",
        metavar="SECTION_ID",
        help="run only the configured section with this id",
    )
    parser.add_argument(
        "--max-board-sections",
        type=int,
        metavar="N",
        help="limit each dataset run to the first N configured board sections; skip static pages",
    )
    parser.add_argument(
        "--max-items",
        type=int,
        metavar="N",
        help="limit the number of documents processed per dataset run",
    )
    parser.add_argument(
        "--no-download-files",
        action="store_true",
        help="keep attachment metadata without downloading files",
    )
    args = parser.parse_args()

    if args.recent_only is not None and args.recent_only <= 0:
        parser.error("--recent-only must be a positive integer")
    if args.max_items is not None and args.max_items <= 0:
        parser.error("--max-items must be a positive integer")
    if args.max_board_sections is not None and args.max_board_sections <= 0:
        parser.error("--max-board-sections must be a positive integer")
    if args.section and args.max_board_sections is not None:
        parser.error("--section cannot be combined with --max-board-sections")
    if args.all and args.dataset not in {None, default_dataset}:
        parser.error("--all cannot be combined with --dataset")
    if args.all and args.section:
        parser.error("--section cannot be combined with --all")
    try:
        registry_audit = audit_registry(args.registry)
    except (OSError, json.JSONDecodeError) as exc:
        print(json.dumps({
            "status": "invalid_registry", "code": "REGISTRY_ERROR",
            "message": str(exc), "retryable": False,
        }, ensure_ascii=False), file=sys.stderr)
        return 1
    if registry_audit.errors:
        print(json.dumps({
            "status": "invalid_registry", "code": "REGISTRY_VALIDATION_ERROR",
            "retryable": False, "errors": registry_audit.errors,
        }, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1
    registry = registry_audit.configs
    try:
        validate_registry_catalog_links(registry, load_site_catalog(args.sites))
    except (OSError, ValueError) as exc:
        print(json.dumps({
            "status": "invalid_registry", "code": "REGISTRY_CATALOG_ERROR",
            "message": str(exc), "retryable": False,
        }, ensure_ascii=False), file=sys.stderr)
        return 1
    if args.all:
        configs = crawl_ready_configs(registry)
        skipped = len(registry) - len(configs)
        print(f"department batch: ready={len(configs)} skipped_not_ready={skipped}")
        if not configs:
            print("department batch failed: no crawl-ready datasets")
            return 1
        exit_codes = [_run_config(config, args) for config in configs]
        return 1 if any(code != 0 for code in exit_codes) else 0
    if not args.dataset:
        parser.error("one of --dataset or --all is required")
    config = registry.get(args.dataset)
    if config is None:
        parser.error(f"unknown department dataset: {args.dataset}")
    if args.section and args.section not in {section.id for section in config.active_sections}:
        parser.error(f"unknown or disabled section for {config.dataset}: {args.section}")
    return _run_config(config, args)


if __name__ == "__main__":
    raise SystemExit(main())

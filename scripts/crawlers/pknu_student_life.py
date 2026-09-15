"""
부경대학교 대학생활 가이드 + E-하나로 크롤러

모드:
  - guide: /main/434 PDF 모음
  - ebook: /ebook/col_life/kor (원본 PDF 탐색, 실패 시 메타만)
  - all: 둘 다

저장:
  - files/pknu_student_life/output/json/<subcategory>/<slug>.json
  - files/pknu_student_life/output/files/<subcategory>/<slug>/*.pdf
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import ssl
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
import urllib3
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.crawlers.common.schema import (  # noqa: E402
    CrawlStats,
    RunResult,
    apply_common_schema,
    attachment_error,
    attachment_text,
    build_attachment,
    content_sha256,
    document_slug,
    log_run_result,
    now_kst,
    sanitize_attachment_filename,
    url_source_id,
)

from scripts.crawlers.common.logging import (  # noqa: E402
    configure_crawler_logging, log_attachment_events, log_event, set_run_id,
)
from scripts.crawlers.common.storage import (  # noqa: E402
    empty_state, get_dataset_paths, load_state_with_migration, save_state_atomic, write_document,
)
from scripts.crawlers.common.reader import remove_redundant_legacy_fields  # noqa: E402

log, log_context = configure_crawler_logging("pknu_student_life", PROJECT_ROOT)

BASE_URL = "https://www.pknu.ac.kr"
GUIDE_URL = f"{BASE_URL}/main/434"
EBOOK_INDEX_URL = f"{BASE_URL}/ebook/col_life/kor/index.html"
EBOOK_BASE_URL = f"{BASE_URL}/ebook/col_life/kor/"

PATHS = get_dataset_paths(PROJECT_ROOT, "pknu_student_life")
OUTPUT_JSON = PATHS.json
OUTPUT_HTML = PATHS.html
OUTPUT_FILES = PATHS.files
OUTPUT_DELETED = PATHS.deleted
STATE_FILE = PATHS.state
LEGACY_STATE_FILES = (PROJECT_ROOT / "state_pknu_student_life.json",)

REQUEST_DELAY = 1.0
REQUEST_TIMEOUT = 60
MIN_PDF_TEXT_CHARS = 80

CATEGORY = "대학생활"
DOC_TYPE = "guide"

EXCLUDE_KEYWORDS = ("대학생활계획서", "콘테스트", "우수작")

SUBCATEGORY_EBOOK = "E-하나로"


class _LegacySSLAdapter(HTTPAdapter):
    def init_poolmanager(self, *args, **kwargs):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        ctx.set_ciphers("DEFAULT@SECLEVEL=1")
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


SECTION_YEAR_RE = re.compile(r"(20\d{2})학년도")


@dataclass
class GuideItem:
    title: str
    pdf_url: str
    media_id: str | None
    subcategory: str
    year: int | None
    section_year: int | None = None
    download_name: str = ""
    source_url: str = GUIDE_URL


def build_session(referer: str = GUIDE_URL) -> requests.Session:
    session = requests.Session()
    session.trust_env = False
    session.verify = False
    session.mount("https://", _LegacySSLAdapter())
    session.mount("http://", _LegacySSLAdapter())
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9",
            "Referer": referer,
        }
    )
    return session


def load_state() -> dict[str, Any]:
    state, origin = load_state_with_migration(PATHS, legacy_paths=LEGACY_STATE_FILES)
    log_event(log, logging.INFO, "state_loaded", path=STATE_FILE, entries=len(state["items"]), origin=origin)
    return state


def save_state(state: dict[str, Any]) -> None:
    save_state_atomic(STATE_FILE, state, "pknu_student_life")
    log_event(log, logging.INFO, "state_saved", path=STATE_FILE, entries=len(state.get("items", {})))


def make_slug(url: str, title: str = "") -> str:
    return hashlib.md5(f"{url}|{title}".encode()).hexdigest()[:12]


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalize_title(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\s*보기\s*$", "", text)
    text = re.sub(r"\(PDF\)\s*$", "", text, flags=re.I).strip()
    return text


def infer_subcategory(title: str) -> str:
    if "예비부경" in title or "입학준비" in title:
        return "예비부경인"
    if "로드맵" in title or "교육과정" in title or "이수" in title:
        return "이수_로드맵"
    return "슬기로운_대학생활"


def extract_section_year(h4_text: str) -> int | None:
    m = SECTION_YEAR_RE.search(h4_text)
    return int(m.group(1)) if m else None


def find_section_year_for_element(element: Any) -> int | None:
    for h4 in element.find_all_previous("h4", class_="subNameH4", limit=15):
        year = extract_section_year(h4.get_text(" ", strip=True))
        if year is not None:
            return year
    return None


def infer_year(
    title: str,
    pdf_url: str = "",
    *,
    section_year: int | None = None,
    download: str = "",
) -> int | None:
    if section_year is not None:
        return section_year
    m = re.search(r"(20\d{2})", title)
    if m:
        return int(m.group(1))
    if download:
        m = re.search(r"(20\d{2})", download)
        if m:
            return int(m.group(1))
    m = re.search(r"/media/(20\d{2})/", pdf_url)
    return int(m.group(1)) if m else None


def infer_date(year: int | None) -> str:
    if year:
        return f"{year}-01-01"
    return ""


def is_excluded(title: str) -> bool:
    return any(k in title for k in EXCLUDE_KEYWORDS)


def fetch(
    session: requests.Session,
    url: str,
    *,
    method: str = "GET",
    data: dict[str, str] | None = None,
    stream: bool = False,
) -> requests.Response:
    time.sleep(REQUEST_DELAY)
    try:
        resp = session.request(
            method, url, data=data, timeout=REQUEST_TIMEOUT, verify=False, stream=stream,
        )
    except requests.RequestException as exc:
        log_event(log, logging.ERROR, "request_failed", url=url, method=method, error=exc, retryable=True)
        raise
    if resp.status_code >= 400:
        log_event(log, logging.ERROR, "request_failed", url=url, method=method, status_code=resp.status_code, retryable=False)
    resp.encoding = resp.encoding or "utf-8"
    return resp


def resolve_media_pdf_url(session: requests.Session, media_id: str) -> str:
    resp = fetch(session, f"{BASE_URL}/common/getMdaId.do", method="POST", data={"no": media_id})
    resp.raise_for_status()
    payload = resp.json()
    path = str(payload.get("response") or "").strip()
    if not path:
        raise ValueError(f"empty media path for id={media_id}")
    return urljoin(BASE_URL, "/upload/" + path.lstrip("/"))


def parse_guide_items_from_html(session: requests.Session, html: str) -> list[GuideItem]:
    soup = BeautifulSoup(html, "lxml")
    pending: list[GuideItem] = []
    seen_urls: set[str] = set()

    for div in soup.select("motion.div.uploadPdf, div.uploadPdf"):
        media_id = div.get("data-id")
        if not media_id:
            continue
        section_year = find_section_year_for_element(div)
        li = div.find_previous("li")
        raw_title = li.get_text(" ", strip=True) if li else f"media-{media_id}"
        title = normalize_title(raw_title)
        if is_excluded(title):
            log.info("[SKIP] %s (contest)", title)
            continue
        pending.append(
            GuideItem(
                title=title,
                pdf_url="",
                media_id=media_id,
                subcategory=infer_subcategory(title),
                year=None,
                section_year=section_year,
            )
        )

    for anchor in soup.select('a[href*=".pdf"]'):
        href = anchor.get("href", "").strip()
        if not href:
            continue
        section_year = find_section_year_for_element(anchor)
        download_name = anchor.get("download", "").strip()
        li = anchor.find_parent("li")
        context = (li.get_text(" ", strip=True) if li else "") or anchor.get_text(" ", strip=True)
        title = normalize_title(context) or download_name or "PDF"
        if is_excluded(title):
            continue
        pdf_url = urljoin(GUIDE_URL, href)
        if pdf_url in seen_urls:
            continue
        seen_urls.add(pdf_url)
        pending.append(
            GuideItem(
                title=title,
                pdf_url=pdf_url,
                media_id=None,
                subcategory=infer_subcategory(title),
                year=None,
                section_year=section_year,
                download_name=download_name,
            )
        )

    for item in pending:
        if item.media_id and not item.pdf_url:
            try:
                item.pdf_url = resolve_media_pdf_url(session, item.media_id)
            except Exception as exc:
                log.warning("getMdaId failed id=%s: %s", item.media_id, exc)
        if item.pdf_url:
            item.year = infer_year(
                item.title,
                item.pdf_url,
                section_year=item.section_year,
                download=item.download_name,
            )

    unique: dict[str, GuideItem] = {}
    for item in pending:
        if not item.pdf_url:
            continue
        if item.pdf_url in unique:
            existing = unique[item.pdf_url]
            if len(item.title) > len(existing.title):
                unique[item.pdf_url] = item
        else:
            unique[item.pdf_url] = item

    return list(unique.values())


def extract_pdf_text(pdf_path: Path) -> str:
    import fitz

    doc = fitz.open(pdf_path)
    parts: list[str] = []
    try:
        for page in doc:
            text = page.get_text("text") or ""
            text = re.sub(r"\s+", " ", text).strip()
            if text:
                parts.append(text)
    finally:
        doc.close()
    return "\n\n".join(parts).strip()


def download_pdf(session: requests.Session, pdf_url: str, dest: Path) -> dict[str, Any]:
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        resp = fetch(session, pdf_url, stream=True)
    except requests.RequestException as exc:
        return build_attachment(
            index=1,
            name=dest.name,
            url=pdf_url,
            project_root=PROJECT_ROOT,
            error=attachment_error("REQUEST_FAILED", exc, True),
        )

    final_url = resp.url
    content_type = resp.headers.get("Content-Type", "")
    if resp.status_code != 200:
        status_code = resp.status_code
        resp.close()
        return build_attachment(
            index=1,
            name=dest.name,
            url=pdf_url,
            final_url=final_url,
            project_root=PROJECT_ROOT,
            content_type=content_type,
            error=attachment_error(
                f"HTTP_{status_code}",
                f"attachment returned HTTP {status_code}",
                status_code == 429 or status_code >= 500,
            ),
        )

    try:
        with dest.open("wb") as f:
            for chunk in resp.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
    except (OSError, requests.RequestException) as exc:
        error_code = "STREAM_FAILED" if isinstance(exc, requests.RequestException) else "SAVE_FAILED"
        return build_attachment(
            index=1,
            name=dest.name,
            url=pdf_url,
            final_url=final_url,
            project_root=PROJECT_ROOT,
            content_type=content_type,
            error=attachment_error(error_code, exc, True),
        )
    finally:
        resp.close()

    return build_attachment(
        index=1,
        name=dest.name,
        url=pdf_url,
        final_url=final_url,
        saved_path=dest,
        downloaded=True,
        project_root=PROJECT_ROOT,
        content_type=content_type,
    )


def save_json(doc: dict[str, Any], subcategory: str, slug: str, raw_html: str | None = None) -> Path:
    path, _ = write_document(PATHS, doc, subcategory, slug, raw_html)
    return path


def process_guide_item(
    session: requests.Session,
    item: GuideItem,
    state: dict[str, Any],
    full_resync: bool,
    raw_html: str | None = None,
) -> tuple[str, dict[str, Any] | None]:
    legacy_slug = make_slug(item.pdf_url, item.title)
    if item.source_url == EBOOK_INDEX_URL:
        source_id = "ebook:col_life"
    elif item.media_id:
        source_id = f"guide:media:{item.media_id}"
    else:
        source_id = f"guide:pdf:{url_source_id(item.pdf_url)}"
    slug = document_slug("pknu_student_life", source_id)
    subcategory = item.subcategory

    filename = sanitize_attachment_filename(Path(urlparse(item.pdf_url).path).name or f"{slug}.pdf")
    file_dir = PATHS.attachment_dir(subcategory, slug)
    pdf_path = file_dir / filename

    old_hash = ""
    items_state: dict[str, Any] = state.setdefault("items", {})
    prev = items_state.get(slug, {}) or items_state.get(legacy_slug, {})
    if prev and not full_resync:
        old_hash = prev.get("content_hash", "")

    attachment = download_pdf(session, item.pdf_url, pdf_path)
    attachment["source_page_url"] = item.source_url
    attachment["source_site"] = BASE_URL
    content = ""
    extracted_characters = 0
    if attachment["downloaded"]:
        try:
            content = extract_pdf_text(pdf_path)
            extracted_characters = len(content)
        except Exception as exc:
            attachment["text"] = attachment_text(
                "failed", extractor="pymupdf", error=exc
            )
            log.warning("[PDF-TEXT-FAILED] %s: %s", item.title, exc)
    c_hash = content_sha256(content)

    current_json_path = PATHS.document_json(subcategory, slug)
    current_schema_exists = False
    if current_json_path.exists():
        try:
            current_doc = json.loads(current_json_path.read_text(encoding="utf-8"))
            current_schema_exists = current_doc.get("schema_version") == "1.0"
        except (OSError, json.JSONDecodeError):
            pass

    if (
        not full_resync
        and attachment["downloaded"]
        and old_hash
        and old_hash == c_hash
        and current_schema_exists
    ):
        log.info("[SKIP] %s (unchanged)", item.title)
        log_event(log, logging.INFO, "document_unchanged", source_id=source_id, url=item.source_url, status="unchanged")
        return "unchanged", None

    if (
        attachment["downloaded"]
        and attachment["text"]["status"] != "failed"
        and len(content) < MIN_PDF_TEXT_CHARS
    ):
        log.warning(
            "[PDF-TEXT-SKIP] %s — extracted %d chars (< %d), metadata only",
            item.title,
            len(content),
            MIN_PDF_TEXT_CHARS,
        )
        attachment["text"] = attachment_text(
            "skipped",
            characters=extracted_characters,
            extractor="pymupdf",
            error=f"extracted_chars_below_{MIN_PDF_TEXT_CHARS}",
        )
        content = ""
    elif attachment["downloaded"] and attachment["text"]["status"] != "failed":
        attachment["text"] = attachment_text(
            "success",
            content=content,
            characters=len(content),
            extractor="pymupdf",
        )

    year = item.year or infer_year(
        item.title,
        item.pdf_url,
        section_year=item.section_year,
        download=item.download_name,
    )
    doc: dict[str, Any] = {
        "slug": slug,
        "title": item.title,
        "date": infer_date(year),
        "url": item.source_url,
        "pdf_url": item.pdf_url,
        "category": CATEGORY,
        "subcategory": subcategory,
        "type": DOC_TYPE,
        "year": year,
        "content": content,
        "content_hash": content_hash(content) if content else content_hash(pdf_path.as_posix()),
        "attachments": [attachment],
        "source_site": BASE_URL,
        "crawled_at": datetime.now().isoformat(),
    }
    if item.media_id:
        doc["media_id"] = item.media_id
    if not content:
        doc["pdf_text_skipped"] = True
        if not attachment["downloaded"]:
            doc["pdf_text_skip_reason"] = "pdf_download_failed"
        elif attachment["text"]["status"] == "failed":
            doc["pdf_text_skip_reason"] = "pdf_text_extraction_failed"
        else:
            doc["pdf_text_skip_reason"] = f"extracted_chars_below_{MIN_PDF_TEXT_CHARS}"

    doc = apply_common_schema(
        doc,
        source_dataset="pknu_student_life",
        source_id=source_id,
        source_site=BASE_URL,
        document_type="guide",
        content_source="pdf_text",
        published_at=doc.get("date"),
        metadata={
            "year": year,
            "media_id": item.media_id,
            "pdf_url": item.pdf_url,
            "legacy_slug": legacy_slug,
            "pdf_text_skipped": doc.get("pdf_text_skipped", False),
            "pdf_text_skip_reason": doc.get("pdf_text_skip_reason"),
        },
        crawled_at=doc.get("crawled_at"),
    )
    remove_redundant_legacy_fields(doc)

    save_json(doc, subcategory, slug, raw_html)
    outcome = "new" if not prev else "updated"
    log_attachment_events(log, doc.get("attachments"), source_id=source_id)
    log_event(log, logging.INFO, "document_saved", source_id=source_id, url=item.source_url, status=outcome)
    items_state[slug] = {
        "slug": slug,
        "content_hash": doc["content_hash"],
        "pdf_url": item.pdf_url,
        "last_seen_at": now_kst(),
    }
    return outcome, doc


def discover_ebook_pdf_url(session: requests.Session) -> str | None:
    candidates: list[str] = []

    paths = [
        "mobile/javascript/config.js",
        "files/mobile/javascript/config.js",
        "mobile/javascript/book_config.js",
    ]
    for rel in paths:
        url = urljoin(EBOOK_BASE_URL, rel)
        resp = fetch(session, url)
        if resp.status_code != 200:
            continue
        text = resp.text
        for match in re.findall(r'["\']([^"\']*\.pdf)["\']', text, re.I):
            candidates.append(urljoin(EBOOK_BASE_URL, match.lstrip("/")))
        for match in re.findall(r"(https?://[^\s\"']+\.pdf)", text, re.I):
            candidates.append(match)

    try:
        idx_resp = fetch(session, EBOOK_INDEX_URL)
        if idx_resp.status_code == 200:
            for match in re.findall(r'["\']([^"\']*\.pdf)["\']', idx_resp.text, re.I):
                candidates.append(urljoin(EBOOK_BASE_URL, match.lstrip("/")))
    except Exception:
        pass

    for rel in [
        "files/col_life.pdf",
        "files/source.pdf",
        "download/col_life.pdf",
        "files/publication.pdf",
    ]:
        url = urljoin(EBOOK_BASE_URL, rel)
        try:
            head = session.head(url, timeout=15, allow_redirects=True, verify=False)
            if head.status_code == 200 and "pdf" in head.headers.get("Content-Type", "").lower():
                candidates.append(url)
        except requests.RequestException:
            pass

    seen: set[str] = set()
    for url in candidates:
        if url in seen:
            continue
        seen.add(url)
        try:
            head = session.head(url, timeout=15, allow_redirects=True, verify=False)
            if head.status_code == 200 and "pdf" in head.headers.get("Content-Type", "").lower():
                return url
        except requests.RequestException:
            continue
    return None


def crawl_ebook(session: requests.Session, state: dict[str, Any], full_resync: bool) -> CrawlStats:
    stats = CrawlStats(discovered=1, requested=1)
    pdf_url = discover_ebook_pdf_url(session)
    title = "국립 부경대학교 대학생활 E-하나로"
    source_id = "ebook:col_life"
    slug = document_slug("pknu_student_life", source_id)
    subcategory = SUBCATEGORY_EBOOK

    if not pdf_url:
        log.warning("[E-BOOK] 원본 PDF 미발견 — 메타데이터만 저장")
        stats.failed += 1
        doc = {
            "slug": slug,
            "title": title,
            "date": "",
            "url": EBOOK_INDEX_URL,
            "pdf_url": None,
            "category": CATEGORY,
            "subcategory": subcategory,
            "type": DOC_TYPE,
            "year": None,
            "content": "",
            "content_hash": content_hash(EBOOK_INDEX_URL),
            "attachments": [],
            "source_site": BASE_URL,
            "crawled_at": datetime.now().isoformat(),
            "pdf_not_found": True,
        }
        doc = apply_common_schema(
            doc,
            source_dataset="pknu_student_life",
            source_id=source_id,
            source_site=BASE_URL,
            document_type="guide",
            content_source="metadata_only",
            metadata={"pdf_not_found": True, "year": None, "pdf_url": None},
            crawled_at=doc.get("crawled_at"),
        )
        remove_redundant_legacy_fields(doc)
        save_json(doc, subcategory, slug)
        state.setdefault("items", {})[slug] = {"slug": slug, "pdf_not_found": True}
        stats.new += 1
        return stats

    log.info("[E-BOOK] PDF 발견: %s", pdf_url)
    item = GuideItem(
        title=title,
        pdf_url=pdf_url,
        media_id=None,
        subcategory=subcategory,
        year=None,
        source_url=EBOOK_INDEX_URL,
    )
    status, doc = process_guide_item(session, item, state, full_resync)
    setattr(stats, status, getattr(stats, status) + 1)
    if doc:
        stats.count_attachments(doc.get("attachments"))
    return stats


# ── Plain-HTML "안내 페이지" siblings of /main/434 ──────────────────────────
# /main/434 (GUIDE_URL) is a PDF-attachment gallery, but the site has many
# sibling pages under the same /main/<id> pattern that are plain HTML
# articles instead (no PDF at all) — e.g. campus contact info, 조기졸업,
# 학점포기(성적자율삭제). These never matched crawl_guide()'s PDF-anchor
# parsing, so they were never collected at all. Real content lives inside
# div#subCont (confirmed by inspecting the rendered page); everything
# outside it is shared site nav/header/footer.
STATIC_PAGE_IDS: tuple[int, ...] = (
    17,   # 캠퍼스안내 (대연/용당 캠퍼스별 연락처)
    92,   # 학적변동 (휴학/복학)
    93,   # 전공제도 (복수전공/부전공/전과)
    94,   # 졸업 (조기졸업/학사학위취득유예/졸업사정)
    95,   # 학점인정안내
    96,   # 성적관리
    97,   # 강의평가 및 성적확인
    98,   # 학·석사연계과정
    99,   # 학적부기재사항정정
    100,  # 교직 및 평생교육사과정
    101,  # 현장실습
    102,  # 등록금안내
    103,  # 장학제도
    104,  # 학자금융자
    110,  # 수강신청 안내
    112,  # 강의계획서 조회
    114,  # 학생증발급
    115,  # 국제학생증발급
    117,  # 국외여행 및 어학연수
    118,  # 복지시설
    119,  # 학생자치기구
    237,  # 졸업안내
    238,  # [공통] 졸업요건 안내자료
    244,  # 성적자율삭제(학점포기)
    262,  # 학생생활관
    449,  # 제증명발급 안내
    481,  # 주차요금
    528,  # 예비군
)

SUBCATEGORY_STATIC_PAGE = "학사안내_페이지"


def fetch_static_page(session: requests.Session, page_id: int) -> tuple[str, str] | None:
    """Fetch a plain-HTML /main/<id> info page and return (title, content) or None."""
    url = f"{BASE_URL}/main/{page_id}"
    resp = fetch(session, url)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "lxml")
    container = soup.select_one("#subCont")
    if container is None:
        return None
    for tag in container(["script", "style"]):
        tag.decompose()
    content = re.sub(r"\n{2,}", "\n", container.get_text("\n", strip=True)).strip()
    title = normalize_title(soup.title.get_text(strip=True)) if soup.title else f"main-{page_id}"
    return title, content


def crawl_static_pages(session: requests.Session, state: dict[str, Any], full_resync: bool) -> CrawlStats:
    stats = CrawlStats(discovered=len(STATIC_PAGE_IDS))
    items_state: dict[str, Any] = state.setdefault("items", {})

    for page_id in STATIC_PAGE_IDS:
        url = f"{BASE_URL}/main/{page_id}"
        log_event(log, logging.DEBUG, "document_discovered", source_id=f"page:{page_id}", url=url)
        stats.requested += 1
        try:
            fetched = fetch_static_page(session, page_id)
        except Exception as exc:
            stats.failed += 1
            log.error("[PAGE] %s 실패: %s", url, exc)
            log_event(log, logging.ERROR, "document_failed", source_id=f"page:{page_id}", url=url, error=exc)
            continue

        if fetched is None:
            stats.failed += 1
            log.warning("[PAGE] %s: #subCont 없음, 스킵", url)
            log_event(log, logging.ERROR, "document_failed", source_id=f"page:{page_id}", url=url, reason="no_subcont")
            continue

        title, content = fetched
        if len(content) < MIN_PDF_TEXT_CHARS:
            stats.failed += 1
            log.warning("[PAGE] %s: 본문 %d자 (너무 짧음), 스킵", url, len(content))
            log_event(log, logging.ERROR, "document_failed", source_id=f"page:{page_id}", url=url, reason="content_too_short")
            continue

        slug = make_slug(url, title)
        c_hash = content_hash(content)
        prev = items_state.get(slug, {})
        if prev and not full_resync and prev.get("content_hash") == c_hash:
            log.info("[PAGE] %s (unchanged)", title)
            stats.unchanged += 1
            continue

        doc: dict[str, Any] = {
            "slug": slug,
            "title": title,
            "date": datetime.now().strftime("%Y-%m-%d"),
            "url": url,
            "pdf_url": None,
            "category": CATEGORY,
            "subcategory": SUBCATEGORY_STATIC_PAGE,
            "type": "page",
            "year": None,
            "content": content,
            "content_hash": c_hash,
            "attachments": [],
            "source_site": BASE_URL,
            "crawled_at": datetime.now().isoformat(),
        }
        save_json(doc, SUBCATEGORY_STATIC_PAGE, slug)
        items_state[slug] = {
            "slug": slug,
            "content_hash": c_hash,
            "url": url,
            "last_seen_at": datetime.now().isoformat(),
        }
        outcome = "new" if not prev else "updated"
        setattr(stats, outcome, getattr(stats, outcome) + 1)
        log_event(log, logging.INFO, "document_saved", source_id=f"page:{page_id}", url=url, status=outcome)
        log.info("[PAGE] %s: %s", outcome, title)

    return stats


def crawl_guide(
    session: requests.Session,
    state: dict[str, Any],
    full_resync: bool,
    limit: int | None,
) -> CrawlStats:
    resp = fetch(session, GUIDE_URL)
    resp.raise_for_status()
    items = parse_guide_items_from_html(session, resp.text)
    log.info("[GUIDE] PDF 대상 %d건 (콘테스트 제외)", len(items))

    discovered = len(items)
    if limit is not None:
        items = items[:limit]

    stats = CrawlStats(discovered=discovered, skipped=discovered - len(items))
    if stats.skipped:
        log_event(log, logging.INFO, "document_skipped", reason="limit", count=stats.skipped)
    for i, item in enumerate(items, start=1):
        log_event(log, logging.DEBUG, "document_discovered", source_id=item.media_id, url=item.source_url, title=item.title)
        log.info("[GUIDE] %d/%d %s", i, len(items), item.title)
        try:
            stats.requested += 1
            status, doc = process_guide_item(session, item, state, full_resync, resp.text)
            setattr(stats, status, getattr(stats, status) + 1)
            if doc:
                stats.count_attachments(doc.get("attachments"))
        except Exception as exc:
            stats.failed += 1
            log.error("[GUIDE] 실패 %s: %s", item.title, exc)
            log_event(log, logging.ERROR, "document_failed", source_id=item.media_id, url=item.source_url, error=exc)
    return stats


def run(mode: str, full_resync: bool, limit: int | None) -> CrawlStats:
    state = load_state()
    session = build_session()

    log.info("=" * 60)
    log.info("student_life 크롤 시작 mode=%s full_resync=%s limit=%s", mode, full_resync, limit)

    total_stats = CrawlStats()
    if mode in ("guide", "all"):
        log_event(log, logging.INFO, "section_started", section="guide")
        stats = crawl_guide(session, state, full_resync, limit if mode == "guide" else None)
        total_stats.add(stats)
        log.info("[GUIDE] 완료: %s", stats)
        log_event(log, logging.INFO, "section_finished", section="guide", stats=stats.to_dict())

    if mode in ("pages", "all"):
        log_event(log, logging.INFO, "section_started", section="pages")
        stats = crawl_static_pages(session, state, full_resync)
        total_stats.add(stats)
        log.info("[PAGE] 완료: %s", stats)
        log_event(log, logging.INFO, "section_finished", section="pages", stats=stats.to_dict())

    if mode in ("ebook", "all"):
        log_event(log, logging.INFO, "section_started", section="ebook")
        if mode == "all":
            limit = None
        stats = crawl_ebook(session, state, full_resync)
        total_stats.add(stats)
        log.info("[EBOOK] 완료: %s", stats)
        log_event(log, logging.INFO, "section_finished", section="ebook", stats=stats.to_dict())

    save_state(state)
    log.info("=" * 60)
    return total_stats


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="부경대 대학생활 가이드 + E-하나로 크롤러")
    parser.add_argument(
        "--mode",
        choices=["guide", "pages", "ebook", "all"],
        default="all",
        help="guide=/main/434, pages=형제 안내페이지, ebook=col_life, all=전부",
    )
    parser.add_argument("--full-resync", action="store_true", help="content_hash 무시하고 재수집")
    parser.add_argument("--reset-state", action="store_true", help="files/pknu_student_life/state.json 초기화")
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="guide 모드에서 처리할 PDF 최대 건수 (스모크 테스트용)",
    )
    return parser.parse_args()


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    args = parse_args()
    limit = args.limit
    if args.mode == "ebook":
        limit = None

    run_mode = "smoke" if args.limit is not None else ("full" if args.full_resync else "incremental")
    result = RunResult(dataset="pknu_student_life", mode=run_mode)
    set_run_id(log_context, result.run_id)
    log_event(log, logging.INFO, "run_started", mode=run_mode, source_mode=args.mode)
    try:
        if args.reset_state:
            save_state_atomic(STATE_FILE, empty_state("pknu_student_life"), "pknu_student_life")
            log_event(log, logging.INFO, "state_saved", path=STATE_FILE, action="reset")
        result.stats = run(args.mode, args.full_resync, limit)
        if result.stats.failed:
            result.add_error("DOCUMENT_FAILURES", f"{result.stats.failed} document(s) failed", retryable=True)
        if result.stats.attachments_failed:
            result.add_error("ATTACHMENT_FAILURES", f"{result.stats.attachments_failed} attachment(s) failed", retryable=True)
        result.finish()
    except KeyboardInterrupt:
        result.finish("cancelled")
    except Exception as exc:
        result.add_error("RUN_INITIALIZATION_FAILED", exc, retryable=True)
        result.finish("failed")
        log_event(log, logging.CRITICAL, "run_finished", status="failed", exc_info=True)
    path = result.save(PROJECT_ROOT)
    if result.status != "failed":
        log_event(log, logging.INFO, "run_finished", status=result.status, stats=result.stats.to_dict(), result_path=path)
    log_run_result(log, result, path)
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())

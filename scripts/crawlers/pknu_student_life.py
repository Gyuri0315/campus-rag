"""
부경대학교 대학생활 가이드 + E-하나로 크롤러

모드:
  - guide: /main/434 PDF 모음
  - ebook: /ebook/col_life/kor (원본 PDF 또는 페이지별 HTML 텍스트)
  - all: 둘 다

저장:
  - files/pknu_main/대학생활/<category>/json/<kind>/<slug>.json
  - files/pknu_main/대학생활/<category>/files/<slug>/pdf/*.pdf
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import shutil
import ssl
import sys
import time
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urljoin, urlparse

import requests
import urllib3
from bs4 import BeautifulSoup, NavigableString
from requests.adapters import HTTPAdapter

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.main.paths import page_json, main_root

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
    archive_document, empty_state, get_dataset_paths, load_state_with_migration,
    save_state_atomic, write_document,
)
from scripts.crawlers.common.reader import remove_redundant_legacy_fields  # noqa: E402
from scripts.crawlers.common.main_static import (  # noqa: E402
    extract_structure, linked_file_ids, table_reference_links,
)
from scripts.crawlers.departments.body_images import collect_body_images, save_body_images  # noqa: E402
from scripts.crawlers import pknu_notice  # noqa: E402
from scripts.extractors.image_ocr import VERSION as IMAGE_OCR_VERSION, extract_image  # noqa: E402
from scripts.extractors.common import extract_blocks  # noqa: E402
from scripts.main.collectors.tuition import (  # noqa: E402
    IFRAME_URL as TUITION_IFRAME_URL, collect_main_102_tuition,
)
from scripts.main.collectors.organization import collect_main_533_org  # noqa: E402
from scripts.main.routes import (  # noqa: E402
    STATIC_PAGE_IDS, BOARD_PAGE_IDS, LINK_HUB_PAGE_IDS, TUITION_PAGE_IDS,
    ORG_PAGE_IDS, REDIRECT_PAGE_TARGETS, FILE_PAGE_IDS,
    ACADEMIC_GUIDE_STATIC_PAGE_IDS, ACADEMIC_INFO_STATIC_PAGE_IDS,
    NO_FOLLOW_LINK_PAGE_IDS,
    STUDENT_LIFE_PAGE_IDS as CONFIGURED_PAGE_IDS,
)

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
MAX_FILE_PAGE_BYTES = 25 * 1024 * 1024

CATEGORY = "대학생활"
DOC_TYPE = "guide"

SUBCATEGORY_ACADEMIC_INFO = "학사정보"
SUBCATEGORY_EBOOK = SUBCATEGORY_ACADEMIC_INFO
EBOOK_HTML_PARSER_VERSION = 2


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


def fetch(
    session: requests.Session,
    url: str,
    *,
    method: str = "GET",
    data: dict[str, str] | None = None,
    stream: bool = False,
    allow_redirects: bool = True,
) -> requests.Response:
    time.sleep(REQUEST_DELAY)
    try:
        resp = session.request(
            method, url, data=data, timeout=REQUEST_TIMEOUT, verify=False,
            stream=stream, allow_redirects=allow_redirects,
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


def guide_item_identity(element: Any, raw_title: str) -> tuple[str, str]:
    """Preserve the contest heading so numbered PDF titles stay meaningful."""
    title = normalize_title(raw_title)
    heading = element.find_previous("h4", class_="subNameH4")
    section = heading.get_text(" ", strip=True) if heading else ""
    if "콘테스트" in section and title.startswith("우수작"):
        year = extract_section_year(section)
        prefix = f"{year}학년도 " if year else ""
        return f"{prefix}대학생활계획서 콘테스트 {title}", "학생생활"
    return title, infer_subcategory(title)


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
        title, subcategory = guide_item_identity(div, raw_title)
        pending.append(
            GuideItem(
                title=title,
                pdf_url="",
                media_id=media_id,
                subcategory=subcategory,
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
        title, subcategory = guide_item_identity(anchor, context or download_name or "PDF")
        pdf_url = urljoin(GUIDE_URL, href)
        if pdf_url in seen_urls:
            continue
        seen_urls.add(pdf_url)
        pending.append(
            GuideItem(
                title=title,
                pdf_url=pdf_url,
                media_id=None,
                subcategory=subcategory,
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
    pdf_path = PATHS.attachment_file(subcategory, slug, filename)

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

    current_json_path = PATHS.document_json(subcategory, slug, kind="attachments")
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

    paths = ["mobile/javascript/config.js"]
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


def parse_ebook_page_count(config: str) -> int:
    match = re.search(r"\bbookConfig\.totalPageCount\s*=\s*(\d+)\s*;", config)
    if not match:
        raise ValueError("E-하나로 뷰어 설정에서 페이지 수를 찾지 못했습니다")
    count = int(match.group(1))
    if not 1 <= count <= 500:
        raise ValueError(f"E-하나로 페이지 수가 비정상적입니다: {count}")
    return count


def clean_ebook_page_text(raw: str, page_number: int) -> str:
    """Remove flipbook layout noise while keeping words and column boundaries."""
    raw = raw.replace("\r\n", "\n").replace("\r", "\n").replace("\u00a0", " ")
    raw = re.sub(r"[\u0000-\u0008\u000b\u000c\u000e-\u001f\ufffd]", "", raw)
    raw = re.sub(r"[\u0400-\u0dff]", "", raw)  # broken embedded-font glyphs
    lines: list[str] = []
    for source_line in raw.split("\n"):
        line = source_line.strip()
        if not line:
            if lines and lines[-1]:
                lines.append("")
            continue
        if line in {"1BSU", "+"}:
            continue
        if re.fullmatch(rf"0?{page_number}\s+대학생활\s*[\"“]?E-하나로[\"”]?", line):
            continue
        if ("대학생활 안내" in line and
                re.fullmatch(rf"Part\s+\d+\s+.+\s+0?{page_number}", line, re.I)):
            continue
        line = re.sub(r"[·.]{4,}", " | ", line)
        line = re.sub(r"[ \t]{3,}", " | ", line)
        line = re.sub(r"[ \t]+", " ", line)
        line = re.sub(r"\( +", "(", line)
        line = re.sub(r" +\)", ")", line)
        line = re.sub(r"(?:\s*\|\s*){2,}", " | ", line).strip(" |")
        if line:
            lines.append(line)
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def parse_ebook_page(html: str, page_number: int) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    text_node = soup.select_one(".text-container pre code")
    image = soup.select_one("#content-middle img")
    if text_node is None and image is None:
        raise ValueError(f"E-하나로 {page_number}쪽의 본문 영역을 찾지 못했습니다")
    raw = text_node.get_text() if text_node else ""
    content = clean_ebook_page_text(raw, page_number)
    hangul_count = len(re.findall(r"[가-힣]", content))
    suspicious_glyphs = len(re.findall(r"[\u0400-\u0dff]", raw))
    return {
        "page_number": page_number,
        "url": urljoin(EBOOK_BASE_URL, f"files/basic-html/page{page_number}.html"),
        "image_url": urljoin(EBOOK_BASE_URL + "files/basic-html/", image["src"]) if image and image.get("src") else None,
        "text_status": "readable" if hangul_count else ("unreadable" if raw.strip() else "image_only"),
        "characters": len(content) if hangul_count else 0,
        "source_characters": len(raw.strip()),
        "suspicious_glyphs": suspicious_glyphs,
        "content": content if hangul_count else "",
    }


def retire_legacy_ebook_metadata(state: dict[str, Any]) -> None:
    legacy_slug = make_slug(EBOOK_INDEX_URL, "국립 부경대학교 대학생활 E-하나로")
    legacy_path = PATHS.document_json("E-하나로", legacy_slug)
    if legacy_path.is_file():
        try:
            legacy = json.loads(legacy_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("[E-BOOK] 구버전 결과 확인 실패: %s", exc)
            return
        if legacy.get("url") != EBOOK_INDEX_URL or legacy.get("content"):
            return
        archived = PATHS.deleted_json("E-하나로", legacy_slug)
        if archived.exists():
            log.warning("[E-BOOK] 구버전 결과 보관 위치가 이미 존재합니다: %s", archived)
            return
        archived.parent.mkdir(parents=True, exist_ok=True)
        legacy_path.replace(archived)
    state.setdefault("items", {}).pop(legacy_slug, None)


def crawl_ebook_html(session: requests.Session, state: dict[str, Any], full_resync: bool) -> CrawlStats:
    stats = CrawlStats(discovered=1, requested=1)
    source_id = "ebook:col_life"
    slug = document_slug("pknu_student_life", source_id)
    config_url = urljoin(EBOOK_BASE_URL, "mobile/javascript/config.js")
    try:
        config = fetch(session, config_url)
        config.raise_for_status()
        page_count = parse_ebook_page_count(config.text)
    except (requests.RequestException, ValueError) as exc:
        log.error("[E-BOOK] 페이지 목록 확인 실패: %s", exc)
        stats.failed = 1
        return stats

    pages: list[dict[str, Any]] = []
    failed_pages: list[int] = []
    for number in range(1, page_count + 1):
        url = urljoin(EBOOK_BASE_URL, f"files/basic-html/page{number}.html")
        try:
            response = fetch(session, url)
            response.raise_for_status()
            pages.append(parse_ebook_page(response.content.decode("utf-8-sig", errors="replace"), number))
        except (requests.RequestException, ValueError) as exc:
            log.error("[E-BOOK] %d쪽 수집 실패: %s", number, exc)
            failed_pages.append(number)

    readable = [page for page in pages if page["text_status"] == "readable"]
    if not readable:
        log.error("[E-BOOK] 읽을 수 있는 본문이 없어 기존 결과를 유지합니다")
        stats.failed = 1
        return stats

    content = "\n\n".join(f"[Page {page['page_number']}]\n{page['content']}" for page in readable)
    old = state.setdefault("items", {}).get(slug, {})
    saved_path = PATHS.document_json(SUBCATEGORY_EBOOK, slug, kind="attachments")
    if failed_pages and saved_path.is_file():
        try:
            previous_content = json.loads(saved_path.read_text(encoding="utf-8")).get("content")
        except (OSError, json.JSONDecodeError):
            previous_content = None
        if previous_content:
            log.error("[E-BOOK] 일부 쪽 수집에 실패하여 기존 본문을 유지합니다")
            stats.failed = 1
            return stats
    digest = content_sha256(content)
    if (not full_resync and old.get("content_hash") == digest
            and old.get("parser_version") == EBOOK_HTML_PARSER_VERSION
            and saved_path.is_file()):
        stats.unchanged = 1
        if failed_pages:
            stats.failed = 1
        retire_legacy_ebook_metadata(state)
        return stats

    warnings = []
    unreadable_pages = [page["page_number"] for page in pages if page["text_status"] != "readable"]
    suspect_pages = [page["page_number"] for page in readable if page["suspicious_glyphs"]]
    if unreadable_pages:
        warnings.append(f"ebook_pages_without_readable_text:{','.join(map(str, unreadable_pages))}")
    if suspect_pages:
        warnings.append(f"ebook_pages_with_suspicious_glyphs:{','.join(map(str, suspect_pages))}")
    if failed_pages:
        warnings.append(f"ebook_pages_failed:{','.join(map(str, failed_pages))}")
        stats.failed = 1
    doc: dict[str, Any] = {
        "title": "국립 부경대학교 대학생활 E-하나로",
        "date": "",
        "url": EBOOK_INDEX_URL,
        "pdf_url": None,
        "category": CATEGORY,
        "subcategory": SUBCATEGORY_EBOOK,
        "type": DOC_TYPE,
        "year": None,
        "content": content,
        "attachments": [],
        "pages": pages,
        "page_count": page_count,
        "parser_version": EBOOK_HTML_PARSER_VERSION,
        "source_site": BASE_URL,
        "crawled_at": now_kst(),
    }
    doc = apply_common_schema(
        doc,
        source_dataset="pknu_student_life",
        source_id=source_id,
        source_site=BASE_URL,
        document_type="guide",
        content_source="ebook_basic_html",
        metadata={"page_count": page_count, "readable_page_count": len(readable), "pdf_url": None},
        crawled_at=doc["crawled_at"],
    )
    doc["crawl"]["warnings"] = warnings
    if failed_pages:
        doc["crawl"]["status"] = "partial_success"
    remove_redundant_legacy_fields(doc)
    save_json(doc, SUBCATEGORY_EBOOK, slug)
    state["items"][slug] = {
        "slug": slug, "content_hash": doc["content_hash"],
        "parser_version": EBOOK_HTML_PARSER_VERSION,
        "url": EBOOK_INDEX_URL, "last_seen_at": now_kst(),
    }
    retire_legacy_ebook_metadata(state)
    setattr(stats, "updated" if old else "new", 1)
    log_event(log, logging.INFO, "document_saved", source_id=source_id,
              url=EBOOK_INDEX_URL, status="updated" if old else "new")
    return stats


def crawl_ebook(session: requests.Session, state: dict[str, Any], full_resync: bool) -> CrawlStats:
    stats = CrawlStats(discovered=1, requested=1)
    pdf_url = discover_ebook_pdf_url(session)
    title = "국립 부경대학교 대학생활 E-하나로"
    source_id = "ebook:col_life"
    slug = document_slug("pknu_student_life", source_id)
    subcategory = SUBCATEGORY_EBOOK

    if not pdf_url:
        log.info("[E-BOOK] 원본 PDF 미발견: 페이지별 HTML 텍스트 수집")
        return crawl_ebook_html(session, state, full_resync)

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
SUBCATEGORY_STATIC_PAGE = "학사안내_페이지"
SUBCATEGORY_BOARD_POST = "학점교류_게시판"
SUBCATEGORY_WEEKLY_MENU = "교내_식당_주간식단표"
STATIC_PARSER_VERSION = 12


def static_page_subcategory(page_id: int) -> str:
    """Route verified pages to their configured site category."""
    from scripts.main.routes import ROUTES
    route = ROUTES.get(page_id)
    if route is None:
        return SUBCATEGORY_STATIC_PAGE
    if route.category_path == ("대학생활", "학사안내"):
        return SUBCATEGORY_STATIC_PAGE
    if route.category_path == ("대학생활", "학사정보"):
        return SUBCATEGORY_ACADEMIC_INFO
    return route.category_path[-1]


def board_post_subcategory(page_id: int) -> str:
    """Store growing board archives separately from static student-life pages."""
    return SUBCATEGORY_WEEKLY_MENU if page_id == 399 else SUBCATEGORY_BOARD_POST


@dataclass(frozen=True)
class StaticPageExtraction:
    title: str
    content: str
    status: str
    reason: str = ""
    warnings: tuple[str, ...] = ()
    structure: dict[str, Any] | None = None
    linked_file_page_ids: tuple[int, ...] = ()
    body_images: tuple[dict[str, Any], ...] = ()
    attachments: tuple[dict[str, str], ...] = ()
    embedded_pdfs: tuple[dict[str, str], ...] = ()
    reference_links: tuple[dict[str, Any], ...] = ()


_STATIC_REMOVE = (
    "script, style, noscript, .subMenu, .subTab, .subTitle, .edtDay, .paging, "
    ".brdSch, .brdAll, .brdBtn, .bdvNav, .c_bdvNav, .share, .sns, .lgnForm"
)
_STATIC_BOARD = ".brdList, .board_list, .board-list, .board-w, #tbl_contents, input[name=bbsId], input[name=bbs_id]"


def parse_static_page_html(html: str, page_id: int) -> StaticPageExtraction:
    """Extract static prose, tables, and CMS process diagrams together."""
    soup = BeautifulSoup(html, "lxml")
    title = normalize_title(soup.title.get_text(strip=True)) if soup.title else f"main-{page_id}"
    container = soup.select_one("#subCont")
    if container is None:
        return StaticPageExtraction(title, "", "needs_review", "no_subcont")
    if container.select_one(_STATIC_BOARD):
        return StaticPageExtraction(title, "", "needs_review", "board_list_not_static")
    file_ids = linked_file_ids(container, f"{BASE_URL}/main/{page_id}", set(FILE_PAGE_IDS))
    body = BeautifulSoup(str(container), "lxml")
    for element in body.select(_STATIC_REMOVE):
        element.decompose()
    image_root = body.select_one("#subCont .content_wrap")
    if image_root is None:
        containers = body.select("#subCont")
        image_root = containers[-1] if containers else body
        image_root = (image_root.find("div", class_="col-lg-12", recursive=False)
                      or image_root)
    omitted_image_count = 0
    if page_id == 262:
        # The residence-hall page has a separate homepage shortcut and PDF viewer.
        # Keep its prose, tables, and inline images without collecting either target.
        for anchor in image_root.select('a[href]'):
            target = urljoin(f"{BASE_URL}/main/{page_id}", anchor.get("href", ""))
            if urlparse(target).hostname == "dormitory.pknu.ac.kr":
                if anchor.parent and "sblTxt" in anchor.parent.get("class", []):
                    anchor.parent.decompose()
                else:
                    anchor.decompose()
        for viewer in image_root.select('.uploadPdf[data-id]'):
            label = viewer.find_previous_sibling('li')
            if label is not None:
                label.decompose()
            viewer.decompose()
        # Two images embedded in the source table point at an unavailable
        # development host; their neighboring table text remains intact.
        for img in image_root.select('img[src]'):
            if urlparse(img['src']).hostname == 'devwww.pknu.ac.kr':
                img.decompose()
                omitted_image_count += 1
    image_wrapper = BeautifulSoup('<div class="sub-content"></div>', "lxml")
    if image_root is not None:
        image_wrapper.select_one(".sub-content").append(BeautifulSoup(str(image_root), "lxml"))
    body_images = tuple(collect_body_images(image_wrapper))
    embedded_pdfs = []
    for element in image_root.select('.uploadPdf[data-id]'):
        label = element.find_previous_sibling('li')
        name = label.get_text(' ', strip=True) if label else f"PDF {element['data-id']}"
        embedded_pdfs.append({"media_id": str(element['data-id']).strip(), "name": name})
    attachments = []
    attachment_urls = set()
    for anchor in image_root.select('a[href]'):
        file_url = urljoin(f"{BASE_URL}/main/{page_id}", anchor.get("href", ""))
        parsed = urlparse(file_url)
        extension = Path(parsed.path).suffix.lower()
        direct_upload = (parsed.path.startswith("/upload/") and
                         extension in {".pdf", ".hwp", ".hwpx", ".pptx", ".docx", ".zip"})
        media_download = (parsed.path == "/mdaDownload.do" and
                          re.fullmatch(r"no=\d+", parsed.query) is not None)
        if (parsed.scheme != "https" or parsed.hostname != "www.pknu.ac.kr"
                or not (direct_upload or media_download)
                or file_url in attachment_urls):
            continue
        attachment_urls.add(file_url)
        attachments.append({"name": anchor.get_text(" ", strip=True) or anchor.get("download")
                            or Path(parsed.path).name, "url": file_url})
    try:
        structure = extract_structure(image_root)
        content = structure["content"]
        reference_links = table_reference_links(image_root, f"{BASE_URL}/main/{page_id}")
    except ValueError as exc:
        fallback = re.sub(r"\n{2,}", "\n", body.get_text("\n", strip=True)).strip()
        return StaticPageExtraction(title, fallback, "needs_review",
                                    f"structure_unverified: {exc}")
    if page_id == 449 and (len(embedded_pdfs) != 2 or any(
            not item["media_id"].isdigit() for item in embedded_pdfs)):
        return StaticPageExtraction(title, content, "needs_review", "embedded_pdf_ids_changed",
                                    structure=structure)
    if page_id == 260 and len(attachments) < 5:
        return StaticPageExtraction(title, content, "needs_review", "accident_files_missing",
                                    structure=structure)
    other_dynamic = body.select_one(
        ".getOrgMngList, #calendar, #loadArea, #direction_daeyeon, #direction_yongdang"
    )
    if other_dynamic or (page_id != 449 and body.select_one('.uploadPdf[data-id]')):
        return StaticPageExtraction(title, content, "needs_review", "dynamic_content_requires_adapter",
                                    structure=structure, linked_file_page_ids=file_ids)
    if body.select_one("img[src], img[data-src]") and not content:
        return StaticPageExtraction(title, content, "needs_review", "image_only_requires_ocr",
                                    structure=structure, linked_file_page_ids=file_ids)
    attachment = body.select_one('.uploadPdf[data-id], a[download], a[href$=".pdf" i], a[href$=".hwp" i], a[href$=".hwpx" i]')
    if attachment and len(content) < MIN_PDF_TEXT_CHARS:
        return StaticPageExtraction(title, content, "needs_review", "attachment_only_requires_extractor",
                                    structure=structure, linked_file_page_ids=file_ids)
    without_links = BeautifulSoup(str(body), "lxml")
    for anchor in without_links.select("a"):
        anchor.decompose()
    prose_length = len(without_links.get_text(" ", strip=True))
    if body.select_one("a[href]") and prose_length < MIN_PDF_TEXT_CHARS:
        return StaticPageExtraction(title, content, "needs_review", "link_hub_not_static",
                                    structure=structure, linked_file_page_ids=file_ids)
    if len(content) < MIN_PDF_TEXT_CHARS:
        return StaticPageExtraction(title, content, "needs_review", "content_too_short",
                                    structure=structure, linked_file_page_ids=file_ids)
    warnings = (f"unavailable_source_images:{omitted_image_count}",) if omitted_image_count else ()
    return StaticPageExtraction(title, content, "ready", warnings=warnings, structure=structure,
                                linked_file_page_ids=file_ids, body_images=body_images,
                                attachments=tuple(attachments),
                                embedded_pdfs=tuple(embedded_pdfs),
                                reference_links=reference_links)


def fetch_static_page(session: requests.Session, page_id: int) -> StaticPageExtraction:
    """Fetch a main-site info page and return its validated static extraction."""
    url = f"{BASE_URL}/main/{page_id}"
    resp = fetch(session, url)
    resp.raise_for_status()
    mime = getattr(resp, "headers", {}).get("Content-Type", "").lower()
    if mime and "text/html" not in mime and "application/xhtml+xml" not in mime:
        return StaticPageExtraction(f"main-{page_id}", "", "needs_review", "non_html_response")
    final_url = getattr(resp, "url", url)
    if final_url != url:
        return StaticPageExtraction(f"main-{page_id}", "", "needs_review", "redirect_destination_unverified")
    return parse_static_page_html(resp.text, page_id)


def download_static_body_images(
    session: requests.Session, page_url: str, subcategory: str, slug: str,
    candidates: tuple[dict[str, Any], ...],
) -> list[dict[str, Any]]:
    """Preserve the original images before saving a static page document."""
    if not candidates:
        return []
    records = save_body_images(
        list(candidates), session=session, page_url=page_url,
        output_dir=PATHS.attachment_dir(subcategory, slug) / "images",
        project_root=PROJECT_ROOT,
    )
    failed = [record for record in records if record["status"] != "saved"]
    if failed:
        raise ValueError(f"body_image_download_failed: {failed[0].get('error')}")
    return records


def recognize_static_body_images(records: list[dict[str, Any]]) -> None:
    """Keep tentative OCR beside its source image, outside verified page text."""
    executable = shutil.which("tesseract") or "C:/Program Files/Tesseract-OCR/tesseract.exe"
    tessdata = PROJECT_ROOT / ".tools/tessdata"
    for record in records:
        try:
            result = extract_image(
                PROJECT_ROOT / record["saved_path"], executable=executable,
                language="kor+eng", tessdata=str(tessdata) if tessdata.is_dir() else None,
                psm=11, restore_spacing=True,
            )
            record["ocr_status"] = result["status"]
            record["ocr_blocks"] = result["blocks"]
            record["ocr_text"] = "\n".join(block["text"] for block in result["blocks"])
            record["ocr_warnings"] = result["warnings"]
        except Exception as exc:
            record["ocr_status"] = "failed"
            record["ocr_blocks"] = []
            record["ocr_text"] = ""
            record["ocr_warnings"] = ["OCR_FAILED"]
            record["ocr_error"] = f"{type(exc).__name__}: {exc}"


def extract_static_attachment_text(records: list[dict[str, Any]]) -> None:
    """Keep each downloaded guide's text beside its file metadata."""
    for record in records:
        if not record.get("downloaded"):
            continue
        if record.get("text", {}).get("status") == "success":
            continue
        try:
            path = PROJECT_ROOT / record["saved_path"]
            if path.suffix.lower() == ".zip":
                with zipfile.ZipFile(path) as archive:
                    members = archive.infolist()
                    if len(members) > 200 or sum(item.file_size for item in members) > 200 * 1024 * 1024:
                        raise ValueError("archive member or uncompressed size limit exceeded")
                    record["archive_members"] = [
                        {"name": item.filename, "size_bytes": item.file_size}
                        for item in members if not item.is_dir()
                    ]
                record["text"] = attachment_text("skipped", extractor="zip_archive_manifest")
                continue
            blocks = extract_blocks(path)
            content = "\n".join(str(block.get("text") or "").strip()
                                for block in blocks if block.get("text")).strip()
            record["text"] = attachment_text(
                "success" if content else "skipped", content=content,
                extractor="scripts.extractors.common.extract_blocks",
            )
        except Exception as exc:
            record["text"] = attachment_text(
                "failed", extractor="scripts.extractors.common.extract_blocks",
                error=f"{type(exc).__name__}: {exc}",
            )


def resolve_static_embedded_pdfs(
    session: requests.Session, records: tuple[dict[str, str], ...],
) -> list[dict[str, str]]:
    """Resolve verified CMS PDF viewer IDs without visiting linked pages."""
    resolved = []
    for record in records:
        url = resolve_media_pdf_url(session, record["media_id"])
        parsed = urlparse(url)
        if (parsed.scheme != "https" or parsed.hostname != "www.pknu.ac.kr"
                or not parsed.path.startswith("/upload/")
                or Path(parsed.path).suffix.lower() != ".pdf"):
            raise ValueError(f"embedded_pdf_url_unverified: {url}")
        resolved.append({"name": record["name"], "url": url,
                         "media_id": record["media_id"]})
    return resolved


def crawl_static_pages(
    session: requests.Session, state: dict[str, Any], full_resync: bool,
    page_ids: tuple[int, ...] | None = None,
    route_results: list[dict[str, Any]] | None = None,
    prepared: dict[int, StaticPageExtraction] | None = None,
) -> CrawlStats:
    selected = STATIC_PAGE_IDS if page_ids is None else page_ids
    stats = CrawlStats(discovered=len(selected))
    items_state: dict[str, Any] = state.setdefault("items", {})

    for page_id in selected:
        url = f"{BASE_URL}/main/{page_id}"
        log_event(log, logging.DEBUG, "document_discovered", source_id=f"page:{page_id}", url=url)
        stats.requested += 1
        try:
            fetched = prepared[page_id] if prepared and page_id in prepared else fetch_static_page(session, page_id)
        except Exception as exc:
            stats.failed += 1
            if route_results is not None:
                route_results.append({"page_id": page_id, "route": "static", "url": url,
                                      "status": "failed", "reason": f"{type(exc).__name__}: {exc}"})
            log.error("[PAGE] %s 실패: %s", url, exc)
            log_event(log, logging.ERROR, "document_failed", source_id=f"page:{page_id}", url=url, error=exc)
            continue

        if fetched.status != "ready":
            stats.failed += 1
            if route_results is not None:
                route_results.append({"page_id": page_id, "route": "static", "url": url,
                                      "status": "needs_review", "reason": fetched.reason})
            log.warning("[PAGE] %s: %s, 스킵", url, fetched.reason)
            log_event(log, logging.ERROR, "document_failed", source_id=f"page:{page_id}", url=url, reason=fetched.reason)
            continue

        title, content = fetched.title, fetched.content
        if len(content) < MIN_PDF_TEXT_CHARS:
            stats.failed += 1
            if route_results is not None:
                route_results.append({"page_id": page_id, "route": "static", "url": url,
                                      "status": "needs_review", "reason": "content_too_short"})
            log.warning("[PAGE] %s: 본문 %d자 (너무 짧음), 스킵", url, len(content))
            log_event(log, logging.ERROR, "document_failed", source_id=f"page:{page_id}", url=url, reason="content_too_short")
            continue

        try:
            media_attachments = resolve_static_embedded_pdfs(session, fetched.embedded_pdfs)
        except (requests.RequestException, ValueError) as exc:
            stats.failed += 1
            if route_results is not None:
                route_results.append({"page_id": page_id, "route": "static", "url": url,
                                      "status": "needs_review", "reason": str(exc)})
            log_event(log, logging.ERROR, "document_failed", source_id=f"page:{page_id}",
                      url=url, error=exc)
            continue

        subcategory = static_page_subcategory(page_id)
        slug = make_slug(url, title)
        c_hash = content_hash(content)
        prev = items_state.get(slug, {})
        output_path = PATHS.document_json(subcategory, slug)
        existing_doc = None
        if output_path.is_file():
            try:
                existing_doc = json.loads(output_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                pass
        try:
            images = download_static_body_images(session, url, subcategory, slug, fetched.body_images)
        except ValueError as exc:
            stats.failed += 1
            if route_results is not None:
                route_results.append({"page_id": page_id, "route": "static", "url": url,
                                      "status": "needs_review", "reason": str(exc)})
            log.warning("[PAGE] %s: %s, 스킵", url, exc)
            continue
        from scripts.main.routes import ROUTES
        attachment_sources = [*fetched.attachments, *media_attachments] if ROUTES[page_id].download_attachments else []
        attachments = pknu_notice.save_attachments(
            session, attachment_sources, subcategory, slug, url,
            existing_doc=existing_doc, reuse_existing=not full_resync,
            file_dir=PATHS.attachment_dir(subcategory, slug),
        )
        extract_static_attachment_text(attachments)
        stats.count_attachments(attachments)
        image_hash = hashlib.sha256(json.dumps(
            [(image["source_url"], image["sha256"]) for image in images],
            ensure_ascii=False,
        ).encode("utf-8")).hexdigest()
        attachment_hash = hashlib.sha256(json.dumps(
            [(item["url"], item["sha256"], item["text"]["status"])
             for item in attachments], ensure_ascii=False,
        ).encode("utf-8")).hexdigest()
        reference_link_hash = hashlib.sha256(json.dumps(
            fetched.reference_links, ensure_ascii=False, sort_keys=True,
        ).encode("utf-8")).hexdigest()
        if (prev and not full_resync and prev.get("content_hash") == c_hash
                and prev.get("image_hash") == image_hash
                and prev.get("attachment_hash") == attachment_hash
                and prev.get("reference_link_hash") == reference_link_hash
                and prev.get("parser_version") == STATIC_PARSER_VERSION and output_path.is_file()):
            log.info("[PAGE] %s (unchanged)", title)
            stats.unchanged += 1
            if route_results is not None:
                route_results.append({"page_id": page_id, "route": "static", "url": url,
                                      "status": "unchanged",
                                      "output": output_path.relative_to(PROJECT_ROOT).as_posix(),
                                      "table_count": fetched.structure["table_count"],
                                      "flow_count": fetched.structure["flow_count"],
                                      "image_count": len(images),
                                      "attachment_count": len(attachments),
                                      "reference_link_count": len(fetched.reference_links),
                                      "linked_file_page_ids": list(fetched.linked_file_page_ids)})
            continue

        recognize_static_body_images(images)
        extraction_warnings = list(fetched.warnings)
        if images:
            extraction_warnings.append("IMAGE_OCR_REVIEW_REQUIRED")
            if any(image["ocr_status"] in {"failed", "empty"} for image in images):
                extraction_warnings.append("IMAGE_OCR_INCOMPLETE")
        if any(not item["downloaded"] for item in attachments):
            extraction_warnings.append("ATTACHMENT_DOWNLOAD_INCOMPLETE")
        if any(item["text"]["status"] == "failed" for item in attachments):
            extraction_warnings.append("ATTACHMENT_TEXT_INCOMPLETE")
        if any(item.get("downloaded") and
               Path(item.get("saved_path") or "").suffix.lower() == ".pdf" and
               item["text"]["characters"] < MIN_PDF_TEXT_CHARS
               for item in attachments):
            extraction_warnings.append("PDF_VISUAL_REVIEW_REQUIRED")
        doc: dict[str, Any] = {
            "slug": slug,
            "title": title,
            "date": datetime.now().strftime("%Y-%m-%d"),
            "url": url,
            "pdf_url": (media_attachments[0]["url"] if media_attachments else
                        f"{BASE_URL}/main/{fetched.linked_file_page_ids[0]}"
                        if fetched.linked_file_page_ids else None),
            "category": CATEGORY,
            "subcategory": subcategory,
            "type": "page",
            "year": None,
            "content": content,
            "content_hash": c_hash,
            "attachments": attachments,
            "linked_files": [{"page_id": file_id, "url": f"{BASE_URL}/main/{file_id}"}
                             for file_id in fetched.linked_file_page_ids],
            "reference_links": list(fetched.reference_links),
            "reference_link_hash": reference_link_hash,
            "embedded_pdf_media_ids": [item["media_id"] for item in fetched.embedded_pdfs],
            "structured_content": fetched.structure,
            "images": images,
            "image_count": len(images),
            "image_hash": image_hash,
            "attachment_hash": attachment_hash,
            "image_ocr_version": IMAGE_OCR_VERSION if images else None,
            "parser_version": STATIC_PARSER_VERSION,
            "extraction_warnings": extraction_warnings,
            "source_site": BASE_URL,
            "crawled_at": datetime.now().isoformat(),
        }
        saved_path = save_json(doc, subcategory, slug)
        items_state[slug] = {
            "slug": slug,
            "content_hash": c_hash,
            "url": url,
            "last_seen_at": datetime.now().isoformat(),
            "parser_version": STATIC_PARSER_VERSION,
            "image_hash": image_hash,
            "attachment_hash": attachment_hash,
            "reference_link_hash": reference_link_hash,
            "subcategory": subcategory,
        }
        outcome = "new" if not prev else "updated"
        setattr(stats, outcome, getattr(stats, outcome) + 1)
        if route_results is not None:
            route_results.append({"page_id": page_id, "route": "static", "url": url,
                                  "status": outcome,
                                  "output": saved_path.relative_to(PROJECT_ROOT).as_posix(),
                                  "table_count": fetched.structure["table_count"],
                                  "flow_count": fetched.structure["flow_count"],
                                  "image_count": len(images),
                                  "attachment_count": len(attachments),
                                  "reference_link_count": len(fetched.reference_links),
                                  "linked_file_page_ids": list(fetched.linked_file_page_ids)})
        log_event(log, logging.INFO, "document_saved", source_id=f"page:{page_id}", url=url, status=outcome)
        log.info("[PAGE] %s: %s", outcome, title)

    return stats


def _html_response(resp: requests.Response, expected_url: str) -> bool:
    mime = resp.headers.get("Content-Type", "").lower()
    return (resp.status_code == 200 and resp.url == expected_url
            and ("text/html" in mime or "application/xhtml+xml" in mime))


def parse_board_intro_html(html: str, page_id: int) -> StaticPageExtraction:
    """Parse the fixed introduction above a board with the shared static parser."""
    soup = BeautifulSoup(html, "lxml")
    body = soup.select_one("#subCont")
    if body is None or body.select_one("table.brdList") is None:
        return StaticPageExtraction(f"main-{page_id}", "", "needs_review", "board_intro_missing")
    for element in body.select("#frmPost, .bdCont, table.brdList, .paging, .brdBtn"):
        element.decompose()
    for child in list(body.children):
        if isinstance(child, NavigableString) and child.strip() in {"subMenu", "bdCont"}:
            child.extract()
    return parse_static_page_html(str(soup), page_id)


def crawl_board_page(
    session: requests.Session, state: dict[str, Any], page_id: int,
    full_resync: bool, max_pages: int = 1,
) -> tuple[CrawlStats, dict[str, Any]]:
    """Collect a board's fixed introduction and its linked detail posts."""
    url = f"{BASE_URL}/main/{page_id}"
    stats = CrawlStats()
    result: dict[str, Any] = {"page_id": page_id, "route": "board", "url": url}
    try:
        first = fetch(session, url)
        if not _html_response(first, url):
            raise ValueError(f"board_list_unverified: HTTP {first.status_code}, {first.url}")
        soup = BeautifulSoup(first.text, "lxml")
        bbs_input = soup.select_one('input[name="bbsId"]')
        bbs_id = (bbs_input.get("value") or "").strip() if bbs_input else ""
        expected_bbs_id = {95: "307", 399: "227"}.get(page_id)
        if bbs_id != expected_bbs_id:
            raise ValueError(f"board_id_changed: {bbs_id!r}")
        _, total_pages = pknu_notice.parse_page_indicator(first.text)
        pages = min(max_pages, total_pages)
        items: dict[str, pknu_notice.ListItem] = {}
        for page in range(1, pages + 1):
            response = first if page == 1 else fetch(session, f"{url}?bbsId={bbs_id}&pageIndex={page}")
            if page > 1 and not _html_response(response, f"{url}?bbsId={bbs_id}&pageIndex={page}"):
                raise ValueError(f"board_page_unverified: page {page}")
            label = "교내 식당 주간식단표" if page_id == 399 else "타대학 이수학점 인정안내"
            found = pknu_notice.parse_list_page(response.text, bbs_id, label)
            if not found:
                raise ValueError(f"board_list_empty: page {page}")
            items.update((item.no, item) for item in found)
        stats.discovered = len(items)
        result.update(bbs_id=bbs_id, total_pages=total_pages, pages_crawled=pages,
                      discovered=len(items), documents=[])
    except (requests.RequestException, ValueError) as exc:
        stats.failed += 1
        result.update(status="failed", reason=str(exc), retryable=isinstance(exc, requests.RequestException))
        log_event(log, logging.ERROR, "section_finished", section=f"main:{page_id}", status="failed", error=exc)
        return stats, result

    if page_id == 95:
        intro_details: list[dict[str, Any]] = []
        intro_stats = crawl_static_pages(
            session, state, full_resync, (page_id,), route_results=intro_details,
            prepared={page_id: parse_board_intro_html(first.text, page_id)},
        )
        stats.add(intro_stats)
        result["intro"] = intro_details[0]

    for post_no, item in items.items():
        post_url = f"{url}?action=view&no={post_no}"
        stats.requested += 1
        try:
            response = fetch(session, post_url)
            if not _html_response(response, post_url):
                raise ValueError(f"detail_response_unverified: HTTP {response.status_code}, {response.url}")
            detail_soup = BeautifulSoup(response.text, "lxml")
            board_input = detail_soup.select_one('#frmPost input[name="bbsId"]')
            post_input = detail_soup.select_one('#frmPost input[name="chkNo"]')
            if (board_input is None or board_input.get("value") != bbs_id
                    or post_input is None or post_input.get("value") != post_no):
                raise ValueError("detail_identity_unverified")
            detail = pknu_notice.parse_detail_page(response.text, item)
            if not detail or (not detail["content"].strip() and not detail["attachments"]):
                raise ValueError("detail_body_missing")
            structure = None
            if page_id == 399:
                body = detail_soup.select_one("div.bdvTxt")
                if body is None:
                    raise ValueError("menu_body_missing")
                structure = extract_structure(body)
                detail["content"] = structure["content"]
            source_id = f"main:{page_id}:{post_no}"
            subcategory = board_post_subcategory(page_id)
            attachments = [build_attachment(
                index=index, name=entry["name"], url=entry["url"], project_root=PROJECT_ROOT,
            ) for index, entry in enumerate(detail["attachments"], start=1)]
            doc = apply_common_schema(
                {"url": post_url, "title": detail["title"], "category": CATEGORY,
                 "subcategory": subcategory, "content": detail["content"],
                 "attachments": attachments,
                 **({"structured_content": structure} if structure else {})},
                source_dataset="pknu_student_life", source_id=source_id,
                source_site=BASE_URL, document_type="notice", content_source="pknu_main_board_html",
                published_at=detail["date"], author=detail["author"],
                metadata={"page_id": page_id, "bbs_id": result["bbs_id"], "post_no": post_no},
            )
            slug = doc["slug"]
            saved_doc = PATHS.document_json(subcategory, slug)
            existing_doc = None
            if saved_doc.is_file():
                try:
                    existing_doc = json.loads(saved_doc.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    pass
            doc["attachments"] = pknu_notice.save_attachments(
                session, detail["attachments"], subcategory, slug, post_url,
                existing_doc=existing_doc, reuse_existing=not full_resync,
                file_dir=PATHS.attachment_dir(subcategory, slug),
            )
            if (existing_doc and existing_doc.get("content_hash") == doc["content_hash"]
                    and existing_doc.get("attachments") == doc["attachments"]
                    and not full_resync):
                stats.unchanged += 1
                outcome = "unchanged"
            else:
                save_json(doc, subcategory, slug)
                outcome = "updated" if existing_doc else "new"
                setattr(stats, outcome, getattr(stats, outcome) + 1)
            state["items"][source_id] = {"slug": slug, "content_hash": doc["content_hash"],
                                         "url": post_url, "last_seen_at": now_kst(),
                                         "subcategory": subcategory}
            stats.count_attachments(doc["attachments"])
            result["documents"].append({"post_no": post_no, "status": outcome,
                                         "slug": slug,
                                         "output": saved_doc.relative_to(PROJECT_ROOT).as_posix(),
                                         "attachments": len(doc["attachments"]),
                                         **({"table_count": structure["table_count"]}
                                            if structure else {})})
            log_event(log, logging.INFO, "document_saved" if outcome != "unchanged" else "document_unchanged",
                      source_id=source_id, url=post_url, status=outcome)
        except (requests.RequestException, ValueError, OSError) as exc:
            stats.failed += 1
            result["documents"].append({"post_no": post_no, "status": "failed", "reason": str(exc)})
            log_event(log, logging.ERROR, "document_failed", source_id=f"main:{page_id}:{post_no}",
                      url=post_url, error=exc)
    result["status"] = "partial_success" if stats.failed else "success"
    return stats, result


def inspect_link_hub(session: requests.Session, page_id: int) -> dict[str, Any]:
    url = f"{BASE_URL}/main/{page_id}"
    result: dict[str, Any] = {"page_id": page_id, "route": "link_hub", "url": url}
    try:
        resp = fetch(session, url)
        if not _html_response(resp, url):
            raise ValueError(f"link_hub_response_unverified: HTTP {resp.status_code}, {resp.url}")
        soup = BeautifulSoup(resp.text, "lxml")
        body = soup.select_one("#subCont")
        if body is None:
            raise ValueError("link_hub_body_missing")
        body = BeautifulSoup(str(body), "lxml")
        for element in body.select(_STATIC_REMOVE):
            element.decompose()
        links = []
        seen = set()
        for element in body.select("a[href], iframe[src]"):
            raw_url = (element.get("href") or element.get("src") or "").strip()
            if not raw_url or raw_url.startswith("#") or urlparse(raw_url).scheme.lower() in {"javascript", "mailto", "tel", "data"}:
                continue
            target = urljoin(url, raw_url)
            if urlparse(target).scheme not in {"http", "https"} or target in seen:
                continue
            seen.add(target)
            links.append({"label": element.get_text(" ", strip=True) or "embedded content",
                          "url": target, "kind": "iframe" if element.name == "iframe" else "link"})
        embedded_pdfs = sorted({str(element.get("data-id")) for element in body.select(".uploadPdf[data-id]")})
        if not links and not embedded_pdfs:
            raise ValueError("link_hub_targets_missing")
        result.update(status="success", links=links, embedded_pdf_media_ids=embedded_pdfs)
    except (requests.RequestException, ValueError) as exc:
        result.update(status="failed", reason=str(exc), retryable=isinstance(exc, requests.RequestException))
    return result


def collect_tuition_faq(
    session: requests.Session, faq_url: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Read the /main/250 board without following its payment tab or body links."""
    first = fetch(session, faq_url)
    if not _html_response(first, faq_url):
        raise ValueError(f"tuition_faq_unverified: HTTP {first.status_code}, {first.url}")
    soup = BeautifulSoup(first.text, "lxml")
    board = soup.select_one('input[name="bbsId"]')
    bbs_id = (board.get("value") or "").strip() if board else ""
    if bbs_id != "86":
        raise ValueError(f"tuition_faq_board_changed: {bbs_id!r}")
    _, total_pages = pknu_notice.parse_page_indicator(first.text)
    if not 1 <= total_pages <= 10:
        raise ValueError(f"tuition_faq_page_count_unverified: {total_pages}")

    items: dict[str, pknu_notice.ListItem] = {}
    for page in range(1, total_pages + 1):
        page_url = faq_url if page == 1 else f"{faq_url}?bbsId={bbs_id}&pageIndex={page}"
        response = first if page == 1 else fetch(session, page_url)
        if not _html_response(response, page_url):
            raise ValueError(f"tuition_faq_list_unverified: page {page}")
        found = pknu_notice.parse_list_page(response.text, bbs_id, "등록금 FAQ")
        if not found:
            raise ValueError(f"tuition_faq_list_empty: page {page}")
        for item in found:
            if item.no in items:
                raise ValueError(f"tuition_faq_duplicate_post: {item.no}")
            items[item.no] = item

    faq_items: list[dict[str, Any]] = []
    documents: list[dict[str, Any]] = []
    for post_no, item in items.items():
        post_url = f"{faq_url}?action=view&no={post_no}"
        response = fetch(session, post_url)
        if not _html_response(response, post_url):
            raise ValueError(f"tuition_faq_detail_unverified: {post_no}")
        detail_soup = BeautifulSoup(response.text, "lxml")
        detail_board = detail_soup.select_one('#frmPost input[name="bbsId"]')
        detail_post = detail_soup.select_one('#frmPost input[name="chkNo"]')
        if (detail_board is None or detail_board.get("value") != bbs_id
                or detail_post is None or detail_post.get("value") != post_no):
            raise ValueError(f"tuition_faq_detail_identity_unverified: {post_no}")
        detail = pknu_notice.parse_detail_page(response.text, item)
        if not detail or (not detail["content"].strip() and not detail["attachments"]):
            raise ValueError(f"tuition_faq_detail_empty: {post_no}")

        source_id = f"main:250:{post_no}"
        slug = document_slug("pknu_student_life", source_id)
        output = PATHS.document_json(SUBCATEGORY_ACADEMIC_INFO, slug, kind="posts")
        existing_doc = None
        if output.is_file():
            try:
                existing_doc = json.loads(output.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                pass
        attachments = pknu_notice.save_attachments(
            session, detail["attachments"], SUBCATEGORY_ACADEMIC_INFO, slug, post_url,
            existing_doc=existing_doc, file_dir=PATHS.attachment_dir(SUBCATEGORY_ACADEMIC_INFO, slug),
        )
        if any(not attachment["downloaded"] for attachment in attachments):
            raise ValueError(f"tuition_faq_attachment_failed: {post_no}")
        parts = [detail["content"].strip()] if detail["content"].strip() else []
        for attachment in attachments:
            saved_path = attachment.get("saved_path") or ""
            if Path(saved_path).suffix.lower() != ".pdf":
                continue
            text_info = attachment.get("text") or {}
            if text_info.get("status") == "success":
                pdf_text = text_info.get("content") or ""
            else:
                try:
                    pdf_text = extract_pdf_text(PROJECT_ROOT / saved_path)
                    attachment["text"] = attachment_text(
                        "success" if pdf_text else "skipped", content=pdf_text, extractor="pymupdf",
                    )
                except Exception as exc:
                    raise ValueError(f"tuition_faq_pdf_text_failed: {post_no}: {exc}") from exc
            if pdf_text:
                parts.append(f"[{attachment['name']}]\n{pdf_text}")
        content = "\n\n".join(parts).strip()
        if not content:
            raise ValueError(f"tuition_faq_text_empty: {post_no}")
        doc = apply_common_schema(
            {"url": post_url, "title": detail["title"], "category": CATEGORY,
             "subcategory": SUBCATEGORY_ACADEMIC_INFO, "tags": ["등록금", "FAQ"],
             "content": content, "attachments": attachments},
            source_dataset="pknu_student_life", source_id=source_id,
            source_site=BASE_URL, document_type="guide", content_source="pknu_main_faq_board",
            published_at=detail["date"], author=detail["author"],
            metadata={"page_id": 250, "parent_page_id": 102, "bbs_id": bbs_id,
                      "post_no": post_no},
        )
        documents.append(doc)
        faq_items.append({
            "post_no": post_no, "title": detail["title"], "url": post_url,
            "date": detail["date"], "content": content, "attachments": attachments,
            "document_path": output.relative_to(PROJECT_ROOT).as_posix(),
        })
    return ({"source_url": faq_url, "bbs_id": bbs_id, "total_pages": total_pages,
             "item_count": len(faq_items), "items": faq_items}, documents)


def crawl_tuition_page(session: requests.Session) -> tuple[CrawlStats, dict[str, Any]]:
    """Store #102 tuition data and the FAQ linked from its own tab."""
    page_id = 102
    url = f"{BASE_URL}/main/{page_id}"
    target = page_json(PROJECT_ROOT, 102)
    stats = CrawlStats(discovered=1, requested=1)
    detail: dict[str, Any] = {"page_id": page_id, "route": "tuition", "url": url,
                              "data_path": target.relative_to(PROJECT_ROOT).as_posix()}
    try:
        response = fetch(session, url)
        if not _html_response(response, url):
            raise ValueError(f"tuition_page_unverified: HTTP {response.status_code}, {response.url}")
        soup = BeautifulSoup(response.text, "lxml")
        frame = soup.select_one('#subCont iframe[title="계열별 등록금 조회"][src]')
        iframe_url = urljoin(url, frame.get("src", "")) if frame else ""
        if iframe_url != TUITION_IFRAME_URL:
            raise ValueError(f"tuition_iframe_unverified: {iframe_url!r}")
        faq_tab = next((anchor for anchor in soup.select("#subCont .subTab a[href]")
                        if anchor.get_text(" ", strip=True).upper() == "FAQ"), None)
        faq_url = urljoin(url, faq_tab.get("href", "")) if faq_tab else ""
        if faq_url != f"{BASE_URL}/main/250":
            raise ValueError(f"tuition_faq_tab_unverified: {faq_url!r}")
        data = collect_main_102_tuition(session, url, iframe_url)
        data.update(category=CATEGORY, subcategory=SUBCATEGORY_ACADEMIC_INFO,
                    page_url=url)
        if (not all(data["counts"][name] for name in ("tuition", "departments", "installments"))
                or "official_checks" not in data or "installment_reconciliation" not in data):
            raise ValueError("tuition_query_returned_no_confirmed_rows: " + "; ".join(data["warnings"]))
        faq, faq_docs = collect_tuition_faq(session, faq_url)
        data["faq"] = faq
        for doc in faq_docs:
            output = PATHS.document_json(SUBCATEGORY_ACADEMIC_INFO, doc["slug"], kind="posts")
            existing_doc = None
            if output.is_file():
                try:
                    existing_doc = json.loads(output.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    pass
            if (existing_doc and existing_doc.get("content_hash") == doc["content_hash"]
                    and existing_doc.get("attachments") == doc["attachments"]
                    and existing_doc.get("title") == doc["title"]
                    and existing_doc.get("published_at") == doc["published_at"]):
                continue
            save_json(doc, SUBCATEGORY_ACADEMIC_INFO, doc["slug"])
        previous = target.is_file()
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(target)
        detail.update(status="partial_success" if data["status"] == "needs_review" else "success",
                      counts=data["counts"], review_counts=data["review_counts"],
                      faq_count=faq["item_count"], faq_pages=faq["total_pages"],
                      warnings=data["warnings"],
                      official_checks={key: data["official_checks"][key] for key in
                                       ("amount_rows_checked", "amount_rows_matched",
                                        "classification_rows_checked", "classification_rows_matched")},
                      installment_group_counts=data["installment_reconciliation"]["group_counts"])
        if previous:
            stats.updated += 1
        else:
            stats.new += 1
    except (requests.RequestException, ValueError, OSError) as exc:
        stats.failed += 1
        detail.update(status="failed", reason=str(exc),
                      retryable=isinstance(exc, requests.RequestException))
    return stats, detail


def crawl_org_page(session: requests.Session) -> tuple[CrawlStats, dict[str, Any]]:
    """Collect /main/533's intro and the 249 organization widget."""
    page_id = 533
    url = f"{BASE_URL}/main/{page_id}"
    target = page_json(PROJECT_ROOT, 533)
    stats = CrawlStats(discovered=1, requested=1)
    detail: dict[str, Any] = {"page_id": page_id, "route": "organization", "url": url,
                              "data_path": target.relative_to(PROJECT_ROOT).as_posix()}
    try:
        response = fetch(session, url)
        if not _html_response(response, url):
            raise ValueError(f"organization_page_unverified: HTTP {response.status_code}, {response.url}")
        data = collect_main_533_org(session, url, response.text)
        if not data.get("staff_count") or not data.get("contact"):
            raise ValueError("organization_content_unverified: " + "; ".join(data["warnings"]))
        previous = target.is_file()
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(target)
        detail.update(status="success" if data["status"] == "collected" else "partial_success",
                      staff_count=data["staff_count"], contact=data["contact"],
                      review_status=data["review_status"], warnings=data["warnings"])
        if previous:
            stats.updated += 1
        else:
            stats.new += 1
    except (requests.RequestException, ValueError, OSError) as exc:
        stats.failed += 1
        detail.update(status="failed", reason=str(exc),
                      retryable=isinstance(exc, requests.RequestException))
    return stats, detail


def inspect_redirect(session: requests.Session, page_id: int) -> dict[str, Any]:
    url = f"{BASE_URL}/main/{page_id}"
    expected = REDIRECT_PAGE_TARGETS[page_id]
    result: dict[str, Any] = {"page_id": page_id, "route": "redirect", "url": url,
                              "expected_destination": expected}
    try:
        first = fetch(session, url, stream=True, allow_redirects=False)
        location = urljoin(url, first.headers.get("Location", ""))
        result.update(http_status=first.status_code, destination=location)
        first.close()
        if first.status_code not in {301, 302, 303, 307, 308} or location != expected:
            raise ValueError("redirect_destination_unverified")
        if not urlparse(location).hostname.endswith(".pknu.ac.kr"):
            raise ValueError("redirect_destination_outside_pknu")
        destination = fetch(session, location, stream=True)
        result.update(destination_http_status=destination.status_code,
                      final_url=destination.url,
                      content_type=destination.headers.get("Content-Type", ""))
        destination.close()
        if not urlparse(result["final_url"]).hostname.endswith(".pknu.ac.kr"):
            raise ValueError("redirect_final_url_outside_pknu")
        if destination.status_code in {401, 403}:
            result.update(status="access_restricted", retryable=False)
        elif destination.status_code == 200:
            result.update(status="reachable", content_verified=False)
        else:
            result.update(status="failed", reason="destination_http_error",
                          retryable=destination.status_code == 429 or destination.status_code >= 500)
    except (requests.RequestException, ValueError) as exc:
        result.update(status="failed", reason=str(exc), retryable=isinstance(exc, requests.RequestException))
    return result


def crawl_file_page(
    session: requests.Session, state: dict[str, Any], page_id: int, full_resync: bool,
) -> tuple[CrawlStats, dict[str, Any]]:
    url = f"{BASE_URL}/main/{page_id}"
    result: dict[str, Any] = {"page_id": page_id, "route": "file", "url": url}
    stats = CrawlStats(discovered=1, requested=1)
    try:
        resp = fetch(session, url, stream=True)
        if resp.status_code != 200 or resp.url != url:
            raise ValueError(f"file_response_unverified: HTTP {resp.status_code}, {resp.url}")
        mime = resp.headers.get("Content-Type", "").split(";", 1)[0].lower()
        disposition = resp.headers.get("Content-Disposition", "")
        match = re.search(r'filename="?([^";]+)', disposition, re.I)
        filename = sanitize_attachment_filename(unquote(match.group(1))) if match else "main-238.pdf"
        if not filename.lower().endswith(".pdf"):
            raise ValueError("file_name_not_pdf")
        chunks = resp.iter_content(chunk_size=256 * 1024)
        first = next(chunks, b"")
        if mime not in {"application/pdf", "application/octet-stream"} or not first.startswith(b"%PDF-"):
            raise ValueError("file_signature_or_mime_unverified")
        slug = document_slug("pknu_student_life", f"main:{page_id}:file")
        dest = PATHS.attachment_file("학사안내_파일", slug, filename)
        dest.parent.mkdir(parents=True, exist_ok=True)
        temporary = dest.with_name(dest.name + ".part")
        try:
            size = len(first)
            if size > MAX_FILE_PAGE_BYTES:
                raise ValueError("file response exceeds size limit")
            digest = hashlib.sha256()
            with temporary.open("wb") as handle:
                handle.write(first)
                digest.update(first)
                for chunk in chunks:
                    if chunk:
                        size += len(chunk)
                        if size > MAX_FILE_PAGE_BYTES:
                            raise ValueError("file response exceeds size limit")
                        handle.write(chunk)
                        digest.update(chunk)
            current_hash = hashlib.sha256(dest.read_bytes()).hexdigest() if dest.is_file() else None
            if current_hash != digest.hexdigest():
                temporary.replace(dest)
        finally:
            temporary.unlink(missing_ok=True)
        attachment = build_attachment(index=1, name=filename, url=url, final_url=resp.url,
                                      saved_path=dest, downloaded=True, content_type=mime,
                                      project_root=PROJECT_ROOT)
        content = extract_pdf_text(dest)
        doc = apply_common_schema(
            {"url": url, "title": "[공통] 졸업요건 안내자료", "category": CATEGORY,
             "subcategory": "학사안내_파일", "content": content,
             "attachments": [attachment], "pdf_url": url},
            source_dataset="pknu_student_life", source_id=f"main:{page_id}:file",
            source_site=BASE_URL, document_type="guide", content_source="pknu_main_pdf",
            metadata={"page_id": page_id, "file_name": filename},
        )
        if len(content) < MIN_PDF_TEXT_CHARS:
            doc["crawl"]["warnings"].append("PDF_TEXT_REQUIRES_REVIEW")
        previous = state.setdefault("items", {}).get(f"file:{page_id}", {})
        outcome = "unchanged" if previous.get("content_hash") == doc["content_hash"] and not full_resync else ("updated" if previous else "new")
        if outcome != "unchanged":
            save_json(doc, "학사안내_파일", slug)
        setattr(stats, outcome, getattr(stats, outcome) + 1)
        stats.count_attachments([attachment])
        state["items"][f"file:{page_id}"] = {"slug": slug, "content_hash": doc["content_hash"],
                                               "url": url, "last_seen_at": now_kst()}
        result.update(status=outcome, content_length=len(content), filename=filename,
                      saved_path=attachment["saved_path"], sha256=attachment["sha256"],
                      size_bytes=attachment["size_bytes"], warnings=doc["crawl"]["warnings"])
    except (requests.RequestException, ValueError, OSError, ImportError) as exc:
        stats.failed += 1
        result.update(status="failed", reason=str(exc), retryable=isinstance(exc, requests.RequestException))
    finally:
        if "resp" in locals():
            resp.close()
    return stats, result


def retire_legacy_static_route(state: dict[str, Any], page_id: int) -> int:
    """Remove only a misrouted static state entry, archiving its old document if present."""
    url = f"{BASE_URL}/main/{page_id}"
    items = state.setdefault("items", {})
    retired = 0
    for key, record in list(items.items()):
        if (not isinstance(record, dict) or record.get("url") != url
                or key == f"file:{page_id}" or record.get("parser_version", 0) >= 2):
            continue
        slug = record.get("slug") or key
        if PATHS.document_json(SUBCATEGORY_STATIC_PAGE, slug).is_file():
            archive_document(PATHS, SUBCATEGORY_STATIC_PAGE, slug)
        del items[key]
        retired += 1
    return retired


def existing_guide_pdf_urls() -> set[str]:
    """Recognize saved legacy guide results without creating duplicate documents."""
    urls: set[str] = set()
    for path in PATHS.iter_document_json():
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if doc.get("url") != GUIDE_URL or not doc.get("pdf_url"):
            continue
        for attachment in doc.get("attachments") or []:
            saved = attachment.get("saved_path")
            if not saved:
                continue
            file_path = Path(saved)
            if not file_path.is_absolute():
                file_path = PROJECT_ROOT / file_path
            if file_path.is_file() and file_path.stat().st_size > 0:
                urls.add(doc["pdf_url"])
                break
    return urls


def crawl_guide(
    session: requests.Session,
    state: dict[str, Any],
    full_resync: bool,
    limit: int | None,
) -> CrawlStats:
    resp = fetch(session, GUIDE_URL)
    resp.raise_for_status()
    items = parse_guide_items_from_html(session, resp.text)
    log.info("[GUIDE] PDF 대상 %d건", len(items))
    existing_urls = existing_guide_pdf_urls()

    discovered = len(items)
    if limit is not None:
        items = items[:limit]

    stats = CrawlStats(discovered=discovered, skipped=discovered - len(items))
    if stats.skipped:
        log_event(log, logging.INFO, "document_skipped", reason="limit", count=stats.skipped)
    for i, item in enumerate(items, start=1):
        log_event(log, logging.DEBUG, "document_discovered", source_id=item.media_id, url=item.source_url, title=item.title)
        log.info("[GUIDE] %d/%d %s", i, len(items), item.title)
        if not full_resync and item.pdf_url in existing_urls:
            stats.requested += 1
            stats.unchanged += 1
            log.info("[GUIDE] existing result: %s", item.title)
            continue
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


def run(
    mode: str, full_resync: bool, limit: int | None,
    page_ids: tuple[int, ...] | None = None, board_pages: int = 1,
    route_report: Path | None = None,
) -> CrawlStats:
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
        selected = set(CONFIGURED_PAGE_IDS if page_ids is None else page_ids)
        static_ids = tuple(page_id for page_id in STATIC_PAGE_IDS if page_id in selected)
        route_results: list[dict[str, Any]] = []
        stats = crawl_static_pages(session, state, full_resync, static_ids,
                                   route_results=route_results)
        selected.update(file_id for detail in route_results
                        if detail["page_id"] not in NO_FOLLOW_LINK_PAGE_IDS
                        for file_id in detail.get("linked_file_page_ids", []))
        for page_id in sorted(selected - set(static_ids)):
            if page_id in BOARD_PAGE_IDS:
                route_stats, detail = crawl_board_page(session, state, page_id, full_resync, board_pages)
                stats.add(route_stats)
            elif page_id in TUITION_PAGE_IDS:
                route_stats, detail = crawl_tuition_page(session)
                stats.add(route_stats)
            elif page_id in ORG_PAGE_IDS:
                route_stats, detail = crawl_org_page(session)
                stats.add(route_stats)
            elif page_id in LINK_HUB_PAGE_IDS:
                detail = inspect_link_hub(session, page_id)
                stats.discovered += 1
                stats.requested += 1
                if detail["status"] == "success":
                    stats.skipped += 1  # Link targets are recorded, not stored as body text.
                else:
                    stats.failed += 1
            elif page_id in REDIRECT_PAGE_TARGETS:
                detail = inspect_redirect(session, page_id)
                stats.discovered += 1
                stats.requested += 1
                if detail["status"] == "reachable":
                    stats.skipped += 1  # External destination content is outside this route.
                else:
                    stats.failed += 1
            elif page_id in FILE_PAGE_IDS:
                route_stats, detail = crawl_file_page(session, state, page_id, full_resync)
                stats.add(route_stats)
            else:
                raise ValueError(f"unconfigured main page: {page_id}")
            if (page_id not in (*TUITION_PAGE_IDS, *ORG_PAGE_IDS) and detail["status"] in
                    {"success", "partial_success", "reachable", "new", "updated", "unchanged"}):
                detail["legacy_static_state_retired"] = retire_legacy_static_route(state, page_id)
            route_results.append(detail)
            log.info("[ROUTE] /main/%s %s: %s", page_id, detail["route"], detail["status"])
            log_event(log, logging.INFO if detail["status"] not in {"failed", "access_restricted"} else logging.ERROR,
                      "section_finished", section=f"main:{page_id}", route=detail["route"],
                      status=detail["status"], reason=detail.get("reason"))
        file_results = {detail["page_id"]: detail for detail in route_results
                        if detail.get("route") == "file"}
        for detail in route_results:
            if detail.get("route") != "static" or not detail.get("output"):
                continue
            linked = [file_results[file_id] for file_id in detail.get("linked_file_page_ids", [])
                      if file_id in file_results]
            if not linked:
                continue
            detail["linked_files"] = linked
            attachments = []
            for file_detail in linked:
                saved_path = file_detail.get("saved_path")
                if not saved_path or file_detail.get("status") not in {"new", "updated", "unchanged"}:
                    continue
                attachments.append(build_attachment(
                    index=len(attachments) + 1, name=file_detail["filename"],
                    url=file_detail["url"], final_url=file_detail["url"],
                    saved_path=PROJECT_ROOT / saved_path, downloaded=True,
                    content_type="application/pdf", project_root=PROJECT_ROOT))
            if attachments:
                path = PROJECT_ROOT / detail["output"]
                doc = json.loads(path.read_text(encoding="utf-8"))
                doc["attachments"] = attachments
                temporary = path.with_suffix(path.suffix + ".tmp")
                temporary.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n",
                                     encoding="utf-8")
                temporary.replace(path)
        report_path = route_report or main_root(PROJECT_ROOT) / "_runs" / "student_life_route_inventory.json"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps({
                "selected_page_ids": sorted(selected), "static_page_ids": list(static_ids),
                "routes": route_results, "stats": stats.to_dict(),
        }, ensure_ascii=False, indent=2), encoding="utf-8")
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

    # These two dynamic routes write standalone data and never change crawler
    # items. Selected-only runs must leave state byte-for-byte.
    if not (mode == "pages" and page_ids is not None
            and set(page_ids) <= set((*TUITION_PAGE_IDS, *ORG_PAGE_IDS))):
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
    parser.add_argument("--reset-state", action="store_true", help="files/pknu_main/_state/pknu_student_life.json 초기화")
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="guide 모드에서 처리할 PDF 최대 건수 (스모크 테스트용)",
    )
    parser.add_argument("--page-ids", help="pages 모드에서 수집할 /main/ 번호 (쉼표 구분)")
    parser.add_argument("--board-pages", type=int, default=1,
                        help="게시판에서 수집할 최신 목록 페이지 수 (기본 1)")
    parser.add_argument("--route-report", type=Path,
                        help="경로 검사 결과 JSON 경로 (기본: files/pknu_main/_runs/student_life_route_inventory.json)")
    return parser.parse_args()


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    args = parse_args()
    if args.page_ids and args.mode != "pages":
        raise SystemExit("--page-ids requires --mode pages")
    if args.board_pages < 1:
        raise SystemExit("--board-pages must be >= 1")
    try:
        page_ids = tuple(dict.fromkeys(int(value.strip()) for value in args.page_ids.split(","))) if args.page_ids else None
    except ValueError as exc:
        raise SystemExit("--page-ids must be comma-separated integers") from exc
    if page_ids is not None and (not page_ids or set(page_ids) - CONFIGURED_PAGE_IDS):
        raise SystemExit(f"unconfigured page ids: {sorted(set(page_ids or ()) - CONFIGURED_PAGE_IDS)}")
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
        result.stats = run(args.mode, args.full_resync, limit,
                           page_ids=page_ids, board_pages=args.board_pages,
                           route_report=args.route_report)
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

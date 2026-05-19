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

LOG_DIR = Path(__file__).resolve().parent / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_DIR / "pknu_student_life_crawler.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)

BASE_URL = "https://www.pknu.ac.kr"
GUIDE_URL = f"{BASE_URL}/main/434"
EBOOK_INDEX_URL = f"{BASE_URL}/ebook/col_life/kor/index.html"
EBOOK_BASE_URL = f"{BASE_URL}/ebook/col_life/kor/"

OUTPUT_JSON = Path("files/pknu_student_life/output/json")
OUTPUT_FILES = Path("files/pknu_student_life/output/files")
OUTPUT_DELETED = Path("files/pknu_student_life/output/deleted")
STATE_FILE = Path("state_pknu_student_life.json")

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
    if STATE_FILE.exists():
        try:
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data.setdefault("items", {})
                return data
        except (OSError, json.JSONDecodeError):
            pass
    return {"items": {}}


def save_state(state: dict[str, Any]) -> None:
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


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
    resp = session.request(
        method,
        url,
        data=data,
        timeout=REQUEST_TIMEOUT,
        verify=False,
        stream=stream,
    )
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


def download_pdf(session: requests.Session, pdf_url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    resp = fetch(session, pdf_url, stream=True)
    resp.raise_for_status()
    with dest.open("wb") as f:
        for chunk in resp.iter_content(chunk_size=1024 * 256):
            if chunk:
                f.write(chunk)


def save_json(doc: dict[str, Any], subcategory: str, slug: str) -> Path:
    out_dir = OUTPUT_JSON / subcategory
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{slug}.json"
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def process_guide_item(
    session: requests.Session,
    item: GuideItem,
    state: dict[str, Any],
    full_resync: bool,
) -> tuple[str, dict[str, Any] | None]:
    slug = make_slug(item.pdf_url, item.title)
    subcategory = item.subcategory

    filename = Path(urlparse(item.pdf_url).path).name or f"{slug}.pdf"
    file_dir = OUTPUT_FILES / subcategory / slug
    pdf_path = file_dir / filename

    old_hash = ""
    items_state: dict[str, Any] = state.setdefault("items", {})
    prev = items_state.get(slug, {})
    if prev and not full_resync:
        old_hash = prev.get("content_hash", "")

    download_pdf(session, item.pdf_url, pdf_path)
    content = extract_pdf_text(pdf_path)
    c_hash = content_hash(content)

    if not full_resync and old_hash and old_hash == c_hash:
        log.info("[SKIP] %s (unchanged)", item.title)
        return "skipped", None

    saved_rel = pdf_path.as_posix()
    if len(content) < MIN_PDF_TEXT_CHARS:
        log.warning(
            "[PDF-TEXT-SKIP] %s — extracted %d chars (< %d), metadata only",
            item.title,
            len(content),
            MIN_PDF_TEXT_CHARS,
        )
        content = ""

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
        "attachments": [
            {
                "name": filename,
                "url": item.pdf_url,
                "saved_path": saved_rel,
            }
        ],
        "source_site": BASE_URL,
        "crawled_at": datetime.now().isoformat(),
    }
    if item.media_id:
        doc["media_id"] = item.media_id
    if not content:
        doc["pdf_text_skipped"] = True
        doc["pdf_text_skip_reason"] = f"extracted_chars_below_{MIN_PDF_TEXT_CHARS}"

    save_json(doc, subcategory, slug)
    items_state[slug] = {
        "slug": slug,
        "content_hash": doc["content_hash"],
        "pdf_url": item.pdf_url,
        "last_seen_at": datetime.now().isoformat(),
    }
    return ("new" if not prev else "updated"), doc


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


def crawl_ebook(session: requests.Session, state: dict[str, Any], full_resync: bool) -> dict[str, int]:
    stats = {"new": 0, "updated": 0, "skipped": 0, "pdf_not_found": 0}
    pdf_url = discover_ebook_pdf_url(session)
    title = "국립 부경대학교 대학생활 E-하나로"
    slug = make_slug(EBOOK_INDEX_URL, title)
    subcategory = SUBCATEGORY_EBOOK

    if not pdf_url:
        log.warning("[E-BOOK] 원본 PDF 미발견 — 메타데이터만 저장")
        stats["pdf_not_found"] = 1
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
        save_json(doc, subcategory, slug)
        state.setdefault("items", {})[slug] = {"slug": slug, "pdf_not_found": True}
        stats["new"] = 1
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
    status, _ = process_guide_item(session, item, state, full_resync)
    stats[status] = stats.get(status, 0) + 1
    return stats


def crawl_guide(
    session: requests.Session,
    state: dict[str, Any],
    full_resync: bool,
    limit: int | None,
) -> dict[str, int]:
    resp = fetch(session, GUIDE_URL)
    resp.raise_for_status()
    items = parse_guide_items_from_html(session, resp.text)
    log.info("[GUIDE] PDF 대상 %d건 (콘테스트 제외)", len(items))

    if limit is not None:
        items = items[:limit]

    stats = {"new": 0, "updated": 0, "skipped": 0, "errors": 0}
    for i, item in enumerate(items, start=1):
        log.info("[GUIDE] %d/%d %s", i, len(items), item.title)
        try:
            status, _ = process_guide_item(session, item, state, full_resync)
            stats[status] = stats.get(status, 0) + 1
        except Exception as exc:
            stats["errors"] += 1
            log.error("[GUIDE] 실패 %s: %s", item.title, exc)
    return stats


def run(mode: str, full_resync: bool, limit: int | None) -> None:
    state = load_state()
    session = build_session()

    log.info("=" * 60)
    log.info("student_life 크롤 시작 mode=%s full_resync=%s limit=%s", mode, full_resync, limit)

    if mode in ("guide", "all"):
        stats = crawl_guide(session, state, full_resync, limit if mode == "guide" else None)
        log.info("[GUIDE] 완료: %s", stats)

    if mode in ("ebook", "all"):
        if mode == "all":
            limit = None
        stats = crawl_ebook(session, state, full_resync)
        log.info("[EBOOK] 완료: %s", stats)

    save_state(state)
    log.info("=" * 60)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="부경대 대학생활 가이드 + E-하나로 크롤러")
    parser.add_argument(
        "--mode",
        choices=["guide", "ebook", "all"],
        default="all",
        help="guide=/main/434, ebook=col_life, all=둘 다",
    )
    parser.add_argument("--full-resync", action="store_true", help="content_hash 무시하고 재수집")
    parser.add_argument("--reset-state", action="store_true", help="state 파일 삭제")
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="guide 모드에서 처리할 PDF 최대 건수 (스모크 테스트용)",
    )
    return parser.parse_args()


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    args = parse_args()
    if args.reset_state and STATE_FILE.exists():
        STATE_FILE.unlink()
        log.info("state 초기화 완료")

    limit = args.limit
    if args.mode == "ebook":
        limit = None

    run(args.mode, args.full_resync, limit)


if __name__ == "__main__":
    main()

"""
부경대학교 본교 공지사항 크롤러
https://www.pknu.ac.kr/main/163

저장 형식:
  - files/pknu_notice/output/json/<category>/<slug>.json
  - files/pknu_notice/output/html/<category>/<slug>.html
  - files/pknu_notice/output/deleted/<category>/<slug>.json

실행:
  - python scripts/main/notice_crawler.py
  - python scripts/main/notice_crawler.py --full-resync
  - python scripts/main/notice_crawler.py --recent-only 5
  - python scripts/main/notice_crawler.py --reset-state
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
from dataclasses import dataclass, field
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
LOG_DIR = PROJECT_ROOT / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_DIR / "main_notice_crawler.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)

BASE_URL = "https://www.pknu.ac.kr"
LIST_URL = f"{BASE_URL}/main/163"
SOURCE_SITE = BASE_URL

OUTPUT_JSON = PROJECT_ROOT / "files" / "pknu_notice" / "output" / "json"
OUTPUT_HTML = PROJECT_ROOT / "files" / "pknu_notice" / "output" / "html"
OUTPUT_DELETED = PROJECT_ROOT / "files" / "pknu_notice" / "output" / "deleted"
STATE_FILE = PROJECT_ROOT / "state_pknu_notice.json"

REQUEST_DELAY = 1.2
LIST_DELAY = 0.8
REQUEST_TIMEOUT = 25
INCREMENTAL_MAX_PAGES = 3
ORPHAN_MISS_THRESHOLD = 3

SUBCATEGORY = "부경대 공지사항"
DOC_TYPE = "notice"

CD_TO_LABEL: dict[str, str] = {
    "10001": "공지사항",
    "10002": "비교과안내",
    "10003": "학사안내",
    "10004": "등록·장학",
    "10007": "초빙·채용",
}

CD_ORDER = ["10001", "10002", "10003", "10004", "10007"]

_NAV_PHRASE_RE = re.compile(
    r"(다음\s*게시글이\s*없습니다\.?|이전\s*게시글이\s*없습니다\.?)",
    re.IGNORECASE,
)


@dataclass
class ListItem:
    no: str
    notice_no: int | None
    is_notice: bool
    list_date: str
    pknu_cd: set[str] = field(default_factory=set)
    categories: set[str] = field(default_factory=set)


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


def build_session() -> requests.Session:
    session = requests.Session()
    session.trust_env = False
    session.verify = False
    adapter = _LegacySSLAdapter()
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8",
            "Referer": LIST_URL,
        }
    )
    return session


def load_state() -> dict[str, Any]:
    if STATE_FILE.exists():
        try:
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data.setdefault("posts", {})
                return data
        except (OSError, json.JSONDecodeError):
            pass
    return {"posts": {}}


def save_state(state: dict[str, Any]) -> None:
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def make_slug(url: str, extra: str = "") -> str:
    return hashlib.md5(f"{url}|{extra}".encode()).hexdigest()[:12]


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def category_dir_name(label: str) -> str:
    return label.replace("·", "_").replace(" ", "_")


def primary_category(labels: list[str]) -> str:
    order = {label: i for i, label in enumerate(CD_TO_LABEL.values())}
    return sorted(labels, key=lambda x: order.get(x, 999))[0]


def fetch(
    session: requests.Session,
    url: str,
    params: dict[str, str] | None = None,
    delay: float = REQUEST_DELAY,
) -> requests.Response | None:
    time.sleep(delay)
    try:
        resp = session.get(url, params=params, timeout=REQUEST_TIMEOUT, verify=False)
        resp.encoding = "utf-8"
        if resp.status_code == 200:
            return resp
        log.warning("HTTP %d → %s", resp.status_code, url)
    except requests.RequestException as exc:
        log.error("요청 실패 %s: %s", url, exc)
    return None


def parse_page_indicator(html: str) -> tuple[int, int]:
    m = re.search(r"<span>(\d+)</span>\s*/\s*(\d+)", html)
    if m:
        return int(m.group(1)), int(m.group(2))
    return 1, 1


def parse_list_page(html: str, cd: str, label: str) -> list[ListItem]:
    soup = BeautifulSoup(html, "lxml")
    items: list[ListItem] = []
    for tr in soup.select("table.brdList tbody tr"):
        link = tr.select_one('a[href*="action=view"]')
        if not link:
            continue
        m = re.search(r"[?&]no=(\d+)", link.get("href", ""))
        if not m:
            continue
        post_no = m.group(1)

        num_el = tr.select_one("td.bdlNum")
        num_text = num_el.get_text(strip=True) if num_el else ""
        is_notice = (
            num_text.upper() == "NOTICE"
            or (num_el and "noti" in (num_el.get("class") or []))
        )
        notice_no: int | None = None
        if not is_notice:
            try:
                notice_no = int(num_text)
            except ValueError:
                pass

        date_el = tr.select_one("td.bdlDate")
        list_date = date_el.get_text(strip=True) if date_el else ""

        file_td = tr.select_one("td.bdlFile")
        has_file = bool(file_td and file_td.find("img"))

        item = ListItem(
            no=post_no,
            notice_no=notice_no,
            is_notice=is_notice,
            list_date=list_date,
            pknu_cd={cd},
            categories={label},
        )
        items.append(item)
    return items


def merge_list_item(index: dict[str, ListItem], item: ListItem) -> None:
    if item.no not in index:
        index[item.no] = item
        return
    existing = index[item.no]
    existing.pknu_cd.update(item.pknu_cd)
    existing.categories.update(item.categories)
    if item.is_notice:
        existing.is_notice = True
    if item.list_date and not existing.list_date:
        existing.list_date = item.list_date
    if item.notice_no is not None and existing.notice_no is None:
        existing.notice_no = item.notice_no


def collect_list_items(
    session: requests.Session,
    cds: list[str],
    max_pages: int | None,
    full_resync: bool,
) -> dict[str, ListItem]:
    index: dict[str, ListItem] = {}
    list_params_base = {
        "bbsId": "2",
        "searchCondition": "",
        "searchKeyword": "",
    }

    for cd in cds:
        label = CD_TO_LABEL[cd]
        log.info("[%s %s] 목록 수집 시작", cd, label)

        first = fetch(
            session,
            LIST_URL,
            params={**list_params_base, "cd": cd, "pageIndex": "1"},
            delay=LIST_DELAY,
        )
        if first is None:
            continue

        _, total_pages = parse_page_indicator(first.text)
        if max_pages is not None:
            last_page = min(max_pages, total_pages)
        elif full_resync:
            last_page = total_pages
        else:
            last_page = min(INCREMENTAL_MAX_PAGES, total_pages)

        log.info("[%s %s] 페이지 1/%d (전체 %d페이지)", cd, label, 1, total_pages)
        for item in parse_list_page(first.text, cd, label):
            merge_list_item(index, item)

        for page in range(2, last_page + 1):
            log.info("[%s %s] 페이지 %d/%d 처리 중...", cd, label, page, total_pages)
            resp = fetch(
                session,
                LIST_URL,
                params={**list_params_base, "cd": cd, "pageIndex": str(page)},
                delay=LIST_DELAY,
            )
            if resp is None:
                break
            page_items = parse_list_page(resp.text, cd, label)
            if not page_items:
                break
            for item in page_items:
                merge_list_item(index, item)

    return index


def extract_body_text(content_el) -> str:
    if content_el is None:
        return ""
    soup_copy = BeautifulSoup(str(content_el), "lxml")
    for el in soup_copy.select(".bdvNav, .brdBtn, .c_bdvNav, .c_bdvBtn"):
        el.decompose()
    text = soup_copy.get_text(" ", strip=True)
    text = _NAV_PHRASE_RE.sub("", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def parse_detail_page(html: str, list_item: ListItem) -> dict[str, Any] | None:
    soup = BeautifulSoup(html, "lxml")
    title_el = soup.select_one("tr.first_noti td.title_b") or soup.select_one("td.title_b")
    if not title_el:
        return None

    title = title_el.get_text(strip=True)
    author = ""
    author_el = soup.select_one("td.text_l.noti_name")
    if author_el:
        author = author_el.get_text(strip=True)
    else:
        for tr in soup.select("tr.noti"):
            tds = tr.find_all("td")
            for i, td in enumerate(tds):
                if "작성자" in td.get_text():
                    if i + 1 < len(tds):
                        author = tds[i + 1].get_text(strip=True)
                    break

    date = list_item.list_date
    for tr in soup.select("tr.noti"):
        tds = tr.find_all("td")
        for i, td in enumerate(tds):
            if "작성일" in td.get_text():
                if i + 1 < len(tds):
                    date = tds[i + 1].get_text(strip=True)
                break
        if date:
            break
    if not date:
        m = re.search(r"\d{4}-\d{2}-\d{2}", soup.get_text(" ", strip=True))
        if m:
            date = m.group()

    body_el = soup.select_one("div.bdvTxt")
    content = extract_body_text(body_el)

    attachments: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    root = soup.select_one("div.bdCont") or soup.body
    if root:
        for a in root.select('a[href*="boardDownload.do"]'):
            href = a.get("href", "").strip()
            if not href or href.startswith("javascript"):
                continue
            abs_url = urljoin(BASE_URL, href)
            if abs_url in seen_urls:
                continue
            seen_urls.add(abs_url)
            name = a.get_text(strip=True) or Path(urlparse(abs_url).path).name
            attachments.append({"name": name, "url": abs_url})

    return {
        "title": title,
        "author": author,
        "date": date,
        "content": content,
        "attachments": attachments,
    }


def json_path_for(category_folder: str, slug: str) -> Path:
    return OUTPUT_JSON / category_folder / f"{slug}.json"


def html_path_for(category_folder: str, slug: str) -> Path:
    return OUTPUT_HTML / category_folder / f"{slug}.html"


def find_existing_doc(slug: str) -> tuple[Path | None, dict | None]:
    if not OUTPUT_JSON.exists():
        return None, None
    for path in OUTPUT_JSON.rglob(f"{slug}.json"):
        try:
            return path, json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
    return None, None


def save_document(doc: dict[str, Any], raw_html: str) -> None:
    folder = category_dir_name(doc["category"])
    json_dir = OUTPUT_JSON / folder
    html_dir = OUTPUT_HTML / folder
    json_dir.mkdir(parents=True, exist_ok=True)
    html_dir.mkdir(parents=True, exist_ok=True)
    slug = doc["slug"]
    (json_dir / f"{slug}.json").write_text(
        json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (html_dir / f"{slug}.html").write_text(raw_html, encoding="utf-8")


def move_to_deleted(json_path: Path, doc: dict[str, Any]) -> None:
    folder = category_dir_name(doc.get("category", "unknown"))
    slug = doc["slug"]
    deleted_dir = OUTPUT_DELETED / folder
    deleted_dir.mkdir(parents=True, exist_ok=True)
    dest = deleted_dir / f"{slug}.json"
    shutil.move(str(json_path), str(dest))
    html_src = html_path_for(folder, slug)
    if html_src.exists():
        html_src.unlink()
    log.info("orphan 이동 → deleted/%s/%s.json", folder, slug)


def build_document(
    list_item: ListItem,
    detail: dict[str, Any],
) -> dict[str, Any]:
    categories = sorted(
        list_item.categories,
        key=lambda x: list(CD_TO_LABEL.values()).index(x) if x in CD_TO_LABEL.values() else 99,
    )
    pknu_cd = sorted(list_item.pknu_cd, key=lambda x: CD_ORDER.index(x) if x in CD_ORDER else 99)
    post_url = f"{LIST_URL}?action=view&no={list_item.no}"
    title = detail["title"]
    content = detail["content"]

    return {
        "slug": make_slug(post_url, title),
        "no": int(list_item.no),
        "notice_no": list_item.notice_no,
        "title": title,
        "date": detail["date"],
        "url": post_url,
        "is_notice": list_item.is_notice,
        "categories": categories,
        "pknu_cd": pknu_cd,
        "category": primary_category(categories),
        "subcategory": SUBCATEGORY,
        "type": DOC_TYPE,
        "author": detail["author"],
        "content": content,
        "content_hash": content_hash(content),
        "attachments": detail["attachments"],
        "source_site": SOURCE_SITE,
        "crawled_at": datetime.now().isoformat(),
    }


def merge_doc_categories(existing: dict[str, Any], new_doc: dict[str, Any]) -> dict[str, Any]:
    merged_cats = sorted(
        set(existing.get("categories", [])) | set(new_doc.get("categories", [])),
        key=lambda x: list(CD_TO_LABEL.values()).index(x) if x in CD_TO_LABEL.values() else 99,
    )
    merged_cd = sorted(
        set(existing.get("pknu_cd", [])) | set(new_doc.get("pknu_cd", [])),
        key=lambda x: CD_ORDER.index(x) if x in CD_ORDER else 99,
    )
    new_doc["categories"] = merged_cats
    new_doc["pknu_cd"] = merged_cd
    new_doc["category"] = primary_category(merged_cats)
    return new_doc


@dataclass
class CrawlStats:
    new: int = 0
    updated: int = 0
    skipped: int = 0
    deleted: int = 0
    errors: int = 0


def crawl_details(
    session: requests.Session,
    index: dict[str, ListItem],
    state: dict[str, Any],
    full_resync: bool,
) -> tuple[CrawlStats, set[str]]:
    stats = CrawlStats()
    seen_slugs: set[str] = set()
    posts_state: dict[str, Any] = state.setdefault("posts", {})

    total = len(index)
    for i, (post_no, list_item) in enumerate(index.items(), start=1):
        post_url = f"{LIST_URL}?action=view&no={post_no}"
        if i % 20 == 0 or i == 1:
            log.info("상세 크롤링 %d/%d (no=%s)", i, total, post_no)

        resp = fetch(session, post_url, delay=REQUEST_DELAY)
        if resp is None:
            stats.errors += 1
            continue

        detail = parse_detail_page(resp.text, list_item)
        if detail is None:
            log.warning("상세 파싱 실패 no=%s", post_no)
            stats.errors += 1
            continue

        doc = build_document(list_item, detail)
        slug = doc["slug"]
        seen_slugs.add(slug)

        existing_path, existing_doc = find_existing_doc(slug)
        if existing_doc:
            doc = merge_doc_categories(existing_doc, doc)
            old_hash = existing_doc.get("content_hash", "")
            if not full_resync and old_hash == doc["content_hash"]:
                # 메타(카테고리)만 갱신된 경우 저장
                if (
                    set(existing_doc.get("categories", [])) == set(doc["categories"])
                    and set(existing_doc.get("pknu_cd", [])) == set(doc["pknu_cd"])
                ):
                    stats.skipped += 1
                    posts_state[post_no] = {
                        "slug": slug,
                        "category": existing_doc.get("category", doc["category"]),
                        "miss_count": 0,
                        "last_seen_at": datetime.now().isoformat(),
                    }
                    continue
                doc["content_hash"] = old_hash if old_hash else doc["content_hash"]

        save_folder = category_dir_name(doc["category"])
        if existing_path and existing_path.parent.name != save_folder:
            # 첫 카테고리 폴더가 바뀐 경우: 기존 파일 제거 후 새 경로에 저장
            try:
                existing_path.unlink(missing_ok=True)
                old_html = existing_path.parent.parent.parent / "html" / existing_path.parent.name / f"{slug}.html"
                if old_html.exists():
                    old_html.unlink()
            except OSError as exc:
                log.warning("기존 파일 삭제 실패 %s: %s", existing_path, exc)

        save_document(doc, resp.text)
        if existing_path and existing_path.exists():
            stats.updated += 1
        else:
            stats.new += 1

        posts_state[post_no] = {
            "slug": slug,
            "category": doc["category"],
            "miss_count": 0,
            "last_seen_at": datetime.now().isoformat(),
        }

    return stats, seen_slugs


def process_orphans(state: dict[str, Any], seen_slugs: set[str], full_resync: bool) -> int:
    if full_resync:
        return 0

    posts_state: dict[str, Any] = state.setdefault("posts", {})
    moved = 0
    for post_no, meta in list(posts_state.items()):
        slug = meta.get("slug", "")
        if not slug:
            continue
        if slug in seen_slugs:
            meta["miss_count"] = 0
            continue

        meta["miss_count"] = int(meta.get("miss_count", 0)) + 1
        if meta["miss_count"] < ORPHAN_MISS_THRESHOLD:
            continue

        json_path, doc = find_existing_doc(slug)
        if json_path is None or doc is None:
            posts_state.pop(post_no, None)
            continue

        move_to_deleted(json_path, doc)
        posts_state.pop(post_no, None)
        moved += 1

    return moved


def run_crawl(
    full_resync: bool = False,
    recent_only: int | None = None,
    only_cd: str | None = None,
) -> CrawlStats:
    cds = CD_ORDER.copy()
    if only_cd:
        if only_cd not in CD_TO_LABEL:
            raise ValueError(f"Unknown cd: {only_cd}")
        cds = [only_cd]

    max_pages = None
    if recent_only is not None:
        max_pages = recent_only
    elif not full_resync:
        max_pages = INCREMENTAL_MAX_PAGES

    session = build_session()
    state = load_state()

    log.info("=" * 60)
    log.info(
        "부경대 공지사항 크롤 시작 (full_resync=%s, max_pages=%s)",
        full_resync,
        max_pages if max_pages else "ALL",
    )

    index = collect_list_items(session, cds, max_pages, full_resync)
    log.info("목록 수집 완료: 고유 글 %d건", len(index))

    stats, seen_slugs = crawl_details(session, index, state, full_resync)
    stats.deleted = process_orphans(state, seen_slugs, full_resync)

    save_state(state)

    log.info("=" * 60)
    log.info(
        "완료: 신규=%d, 업데이트=%d, 스킵=%d, 삭제=%d, 오류=%d",
        stats.new,
        stats.updated,
        stats.skipped,
        stats.deleted,
        stats.errors,
    )
    log.info("=" * 60)
    return stats


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="부경대 본교 공지사항 크롤러")
    parser.add_argument(
        "--full-resync",
        action="store_true",
        help="전체 페이지 재수집",
    )
    parser.add_argument(
        "--recent-only",
        type=int,
        metavar="N",
        default=None,
        help="카테고리별 최근 N페이지만 목록 수집",
    )
    parser.add_argument(
        "--reset-state",
        action="store_true",
        help="state_pknu_notice.json 초기화",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="1회 실행 (스케줄러 없음, 기본 동작과 동일)",
    )
    parser.add_argument(
        "--only-cd",
        type=str,
        default=None,
        help="단일 카테고리만 수집 (예: 10001)",
    )
    return parser.parse_args()


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    args = parse_args()
    if args.reset_state and STATE_FILE.exists():
        STATE_FILE.unlink()
        log.info("state_pknu_notice.json 초기화 완료")

    run_crawl(
        full_resync=args.full_resync,
        recent_only=args.recent_only,
        only_cd=args.only_cd,
    )


if __name__ == "__main__":
    main()

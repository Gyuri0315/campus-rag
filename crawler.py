"""
부경대학교 컴퓨터·인공지능공학부 홈페이지 크롤러
https://ce.pknu.ac.kr/ce/1

크롤링 대상:
  - 공지사항: 학과공지, 대학원공지, 산업대학원공지, 교육대학원공지, 자료실
  - 학부안내: 학부소개(전공별 포함), 교육목적및인재상, 교수진, 졸업및진로, 찾아오시는길
  - 학사안내: 교육과정(전공별 포함), 모듈형 교육과정, 졸업요건

저장 형식:
  - FILES/output/json/<category>/<slug>.json
  - FILES/output/html/<category>/<slug>.html

증분 크롤링:
  - state.json 에 게시판별 마지막으로 수집한 게시글 번호를 저장
  - 재실행 시 이미 수집한 게시글 이후부터만 크롤링 (신규 게시글만 수집)
  - 최초 실행은 INITIAL_MAX_PAGES 페이지까지만 수집

자동 스케줄링:
  - 매일 오전 09:00 자동 실행 (schedule 라이브러리)
  - --once 옵션으로 1회 즉시 실행 가능
"""

import argparse
import hashlib
import json
import logging
import re
import ssl
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

import requests
import schedule
import urllib3
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ─── 로깅 설정 ────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("crawler.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)

# ─── 경로 및 상수 ─────────────────────────────────────────────────────────────
BASE_URL = "https://ce.pknu.ac.kr"
OUTPUT_JSON = Path("FILES/output/json")
OUTPUT_HTML = Path("FILES/output/html")
OUTPUT_FILES = Path("FILES/output/files")
STATE_FILE = Path("state.json")

REQUEST_DELAY = 0.8      # 게시글 상세 요청 간 딜레이(초)
LIST_DELAY = 0.5         # 목록 페이지 요청 간 딜레이(초)
REQUEST_TIMEOUT = 20     # HTTP 타임아웃(초)
INITIAL_MAX_PAGES = 10   # 최초 실행 시 게시판별 최대 크롤링 페이지 수
INCREMENTAL_MAX_PAGES = 3  # 증분 실행 시 최대 확인 페이지 수

# ─── 섹션 설정 ────────────────────────────────────────────────────────────────
# is_board=True  → 게시판 (목록+상세 페이지, 페이지네이션 있음)
# is_board=False → 정적 페이지 (단일 페이지, 매번 재수집)
SECTIONS = [
    # ── 공지사항 ────────────────────────────────────────────────────────────
    {
        "name": "학과공지",
        "category": "공지사항",
        "url": f"{BASE_URL}/ce/1814",
        "bbs_id": "2400536",
        "type": "notice",
        "is_board": True,
    },
    {
        "name": "대학원공지",
        "category": "공지사항",
        "url": f"{BASE_URL}/ce/1815",
        "bbs_id": "2400537",
        "type": "notice",
        "is_board": True,
    },
    {
        "name": "산업대학원공지",
        "category": "공지사항",
        "url": f"{BASE_URL}/ce/2425",
        "bbs_id": "2400690",
        "type": "notice",
        "is_board": True,
    },
    {
        "name": "교육대학원공지",
        "category": "공지사항",
        "url": f"{BASE_URL}/ce/2426",
        "bbs_id": "2400691",
        "type": "notice",
        "is_board": True,
    },
    {
        "name": "자료실",
        "category": "공지사항",
        "url": f"{BASE_URL}/ce/1817",
        "bbs_id": "2400539",
        "type": "resource",
        "is_board": True,
    },
    # ── 학부안내 ────────────────────────────────────────────────────────────
    {
        "name": "학부소개",
        "category": "학부안내",
        "url": f"{BASE_URL}/ce/1803",
        "type": "guide",
        "is_board": False,
    },
    {
        "name": "컴퓨터공학전공소개",
        "category": "학부안내",
        "url": f"{BASE_URL}/ce/4942",
        "type": "guide",
        "is_board": False,
    },
    {
        "name": "인공지능전공소개",
        "category": "학부안내",
        "url": f"{BASE_URL}/ce/4943",
        "type": "guide",
        "is_board": False,
    },
    {
        "name": "교육목적및인재상",
        "category": "학부안내",
        "url": f"{BASE_URL}/ce/1804",
        "type": "guide",
        "is_board": False,
    },
    {
        "name": "교수진",
        "category": "학부안내",
        "url": f"{BASE_URL}/ce/1805",
        "type": "guide",
        "is_board": False,
    },
    {
        "name": "졸업후진로",
        "category": "학부안내",
        "url": f"{BASE_URL}/ce/1806",
        "type": "guide",
        "is_board": False,
    },
    {
        "name": "찾아오시는길",
        "category": "학부안내",
        "url": f"{BASE_URL}/ce/1807",
        "type": "guide",
        "is_board": False,
    },
    # ── 학사안내 ────────────────────────────────────────────────────────────
    {
        "name": "교육과정",
        "category": "학사안내",
        "url": f"{BASE_URL}/ce/4945",
        "type": "curriculum",
        "is_board": False,
    },
    {
        "name": "컴퓨터공학전공교육과정",
        "category": "학사안내",
        "url": f"{BASE_URL}/ce/1808",
        "type": "curriculum",
        "is_board": False,
    },
    {
        "name": "인공지능전공교육과정",
        "category": "학사안내",
        "url": f"{BASE_URL}/ce/6933",
        "type": "curriculum",
        "is_board": False,
    },
    {
        "name": "모듈형교육과정",
        "category": "학사안내",
        "url": f"{BASE_URL}/ce/7086",
        "type": "curriculum",
        "is_board": False,
    },
    {
        "name": "졸업요건",
        "category": "학사안내",
        "url": f"{BASE_URL}/ce/2889",
        "type": "curriculum",
        "is_board": False,
    },
]


# ─── HTTP 세션 ────────────────────────────────────────────────────────────────
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
    session.verify = False
    # 레거시 SSL 핸드셰이크 오류 우회 어댑터 적용
    _adapter = _LegacySSLAdapter()
    session.mount("https://", _adapter)
    session.mount("http://", _adapter)
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
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


# ─── 유틸리티 ─────────────────────────────────────────────────────────────────
def make_slug(url: str, extra: str = "") -> str:
    """URL + 추가 키로 고유 파일명 생성 (MD5 앞 12자)"""
    return hashlib.md5(f"{url}|{extra}".encode()).hexdigest()[:12]


def safe_text(el) -> str:
    return el.get_text(" ", strip=True) if el else ""


def ensure_dirs(category: str) -> tuple[Path, Path]:
    jd = OUTPUT_JSON / category
    hd = OUTPUT_HTML / category
    jd.mkdir(parents=True, exist_ok=True)
    hd.mkdir(parents=True, exist_ok=True)
    return jd, hd


def ensure_file_dir(category: str, slug: str) -> Path:
    fd = OUTPUT_FILES / category / slug
    fd.mkdir(parents=True, exist_ok=True)
    return fd


def save_document(doc: dict, raw_html: str) -> None:
    json_dir, html_dir = ensure_dirs(doc["category"])
    slug = doc["slug"]
    (json_dir / f"{slug}.json").write_text(
        json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (html_dir / f"{slug}.html").write_text(raw_html, encoding="utf-8")


# ─── HTTP 요청 ────────────────────────────────────────────────────────────────
def fetch(
    session: requests.Session,
    url: str,
    params: dict | None = None,
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


# ─── 게시판 파싱 ──────────────────────────────────────────────────────────────
def sanitize_filename(filename: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", filename).strip(" .")
    return cleaned or "attachment"


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
            continue

        try:
            time.sleep(0.2)
            resp = session.get(
                file_url,
                timeout=REQUEST_TIMEOUT,
                stream=True,
                headers={"Referer": source_page_url or BASE_URL},
            )
        except requests.RequestException as exc:
            log.warning("첨부파일 다운로드 실패 %s: %s", file_url, exc)
            results.append(
                {
                    **attachment,
                    "saved_path": "",
                    "downloaded": False,
                    "source_page_url": source_page_url,
                    "source_site": BASE_URL,
                }
            )
            continue

        if resp.status_code != 200:
            log.warning("첨부파일 HTTP %d %s", resp.status_code, file_url)
            resp.close()
            results.append(
                {
                    **attachment,
                    "saved_path": "",
                    "downloaded": False,
                    "source_page_url": source_page_url,
                    "source_site": BASE_URL,
                }
            )
            continue

        filename = extract_filename(resp, file_url, attachment.get("name", ""))
        content_type = (resp.headers.get("Content-Type", "") or "").lower()
        if "text/html" in content_type or "application/xhtml+xml" in content_type:
            log.info("HTML 응답은 첨부로 저장하지 않음: %s", file_url)
            resp.close()
            continue

        if filename.lower().endswith((".html", ".htm", ".shtml")):
            log.info("HTML 파일명은 첨부로 저장하지 않음: %s", filename)
            resp.close()
            continue

        if "." not in filename:
            fallback_ext = Path(urlparse(resp.url).path).suffix
            if fallback_ext:
                filename = f"{filename}{fallback_ext}"

        if filename in used_names:
            base = Path(filename).stem
            ext = Path(filename).suffix
            filename = f"{base}_{idx}{ext}"
        used_names.add(filename)

        output_file = file_dir / filename
        try:
            with output_file.open("wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
            saved_path = str(output_file.as_posix())
            downloaded = True
        except OSError as exc:
            log.warning("첨부파일 저장 실패 %s: %s", output_file, exc)
            saved_path = ""
            downloaded = False
        finally:
            resp.close()

        results.append(
            {
                **attachment,
                "saved_path": saved_path,
                "downloaded": downloaded,
                "source_page_url": source_page_url,
                "source_site": BASE_URL,
                "downloaded_from_url": resp.url,
                "content_type": content_type,
            }
        )

    return results


def parse_list_page(soup: BeautifulSoup, board_url: str) -> list[dict]:
    """게시판 목록에서 게시글 정보(번호·제목·날짜·URL) 추출"""
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

            # ce.pknu.ac.kr/ce/<숫자> 형태의 게시판 내부 링크 제외
            if re.fullmatch(
                r"https?://ce\.pknu\.ac\.kr/ce/\d+", abs_url.split("?")[0]
            ):
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
) -> tuple[int, int]:
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
    last_known_no: int = state.get(state_key, {}).get("last_no", 0)
    max_pages = INITIAL_MAX_PAGES if is_initial else INCREMENTAL_MAX_PAGES

    log.info(
        "[게시판] %s | last_no=%d | max_pages=%d",
        name, last_known_no, max_pages
    )

    saved = 0
    new_max_no = last_known_no

    for page in range(1, max_pages + 1):
        params: dict = {"pageIndex": page}
        if bbs_id:
            params["bbsId"] = bbs_id

        resp = fetch(session, board_url, params=params, delay=LIST_DELAY)
        if resp is None:
            break

        soup = BeautifulSoup(resp.text, "lxml")
        items = parse_list_page(soup, board_url)
        if not items:
            log.info("  p%d: 게시글 없음 → 종료", page)
            break

        # 이번 페이지의 일반 게시글 번호 목록 (고정글 제외)
        regular = [it for it in items if not it["is_notice"] and it["post_no"] is not None]
        if not regular:
            log.info("  p%d: 일반 게시글 없음 → 종료", page)
            break

        page_max_no = max(it["post_no"] for it in regular)
        page_min_no = min(it["post_no"] for it in regular)
        log.info("  p%d: %d개 (no %d~%d)", page, len(regular), page_min_no, page_max_no)

        # 이미 수집한 게시글만 있으면 중단
        if page_max_no <= last_known_no:
            log.info("  p%d: 모두 기수집 → 종료 (last_no=%d)", page, last_known_no)
            break

        new_max_no = max(new_max_no, page_max_no)

        # 신규 게시글만 상세 크롤링
        for item in items:
            # 고정글(NOTICE) 포함 수집
            post_no = item["post_no"]
            if post_no is not None and post_no <= last_known_no:
                continue  # 이미 수집함

            post_resp = fetch(session, item["post_url"])
            if post_resp is None:
                continue

            post_soup = BeautifulSoup(post_resp.text, "lxml")
            view = parse_view_page(post_soup, item["post_url"], item)
            if view is None:
                continue

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
            save_document(doc, post_resp.text)
            saved += 1

        # 이번 페이지에 last_known_no 이하의 번호가 포함됐으면 다음 페이지는 불필요
        if page_min_no <= last_known_no:
            log.info("  p%d: 일부 기수집 → 다음 페이지 불필요", page)
            break

    log.info("[게시판] %s 완료 → 신규 %d건 | new_max_no=%d", name, saved, new_max_no)
    return saved, new_max_no


# ─── 정적 페이지 크롤러 ───────────────────────────────────────────────────────
def crawl_static(session: requests.Session, section: dict) -> int:
    """정적 소개/안내 페이지를 크롤링해 저장한다."""
    page_url = section["url"]
    category = section["category"]
    name = section["name"]
    doc_type = section["type"]

    log.info("[정적] %s (%s)", name, page_url)
    resp = fetch(session, page_url, delay=LIST_DELAY)
    if resp is None:
        return 0

    soup = BeautifulSoup(resp.text, "lxml")

    # 제목: breadcrumb 마지막 항목 또는 <title>
    breadcrumb = soup.select(".a_sbtNav dd")
    title = breadcrumb[-1].get_text(strip=True) if breadcrumb else name
    if not title:
        title = safe_text(soup.select_one("title")) or name

    # 본문: 우선순위 순으로 탐색
    content_el = (
        soup.select_one(".a_bdCont")
        or soup.select_one("#contents")
        or soup.select_one(".contents")
        or soup.select_one("main")
        or soup.select_one(".container")
    )
    content_text = extract_body_content(content_el) if content_el else safe_text(soup.body)

    slug = make_slug(page_url, name)
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
        "attachments": [],
        "crawled_at": datetime.now().isoformat(),
    }
    save_document(doc, resp.text)
    log.info("[정적] %s 완료", name)
    return 1


# ─── 전체 크롤링 실행 ─────────────────────────────────────────────────────────
def run_crawl() -> None:
    start = datetime.now()
    log.info("=" * 60)
    log.info("크롤링 시작: %s", start.strftime("%Y-%m-%d %H:%M:%S"))

    state = load_state()
    is_initial = not bool(state)  # state.json 없으면 최초 실행
    if is_initial:
        log.info("최초 실행: 게시판당 최대 %d페이지 수집", INITIAL_MAX_PAGES)
    else:
        log.info("증분 실행: 신규 게시글만 수집 (최대 %d페이지)", INCREMENTAL_MAX_PAGES)
    log.info("=" * 60)

    session = build_session()
    total_saved = 0
    new_state = dict(state)

    for section in SECTIONS:
        try:
            if section["is_board"]:
                saved, new_max_no = crawl_board(session, section, state, is_initial)
                total_saved += saved
                # 상태 갱신 (max_no 증가 시에만)
                key = section["url"]
                prev_no = state.get(key, {}).get("last_no", 0)
                if new_max_no > prev_no:
                    new_state[key] = {
                        "last_no": new_max_no,
                        "name": section["name"],
                        "updated_at": datetime.now().isoformat(),
                    }
            else:
                saved = crawl_static(session, section)
                total_saved += saved
        except Exception as exc:
            log.error("섹션 오류 [%s]: %s", section["name"], exc, exc_info=True)

    save_state(new_state)
    elapsed = (datetime.now() - start).total_seconds()
    log.info("=" * 60)
    log.info(
        "크롤링 완료: 총 %d건 저장 | 소요 %.1f초 | state 저장됨",
        total_saved, elapsed,
    )
    log.info("=" * 60)


# ─── 스케줄러 ─────────────────────────────────────────────────────────────────
def run_scheduler() -> None:
    log.info("스케줄러 시작: 매일 오전 09:00 자동 크롤링")
    schedule.every().day.at("09:00").do(run_crawl)
    run_crawl()          # 시작 즉시 1회 실행
    while True:
        schedule.run_pending()
        time.sleep(30)


# ─── 진입점 ───────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="부경대 컴퓨터·인공지능공학부 홈페이지 크롤러"
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="스케줄링 없이 즉시 1회만 실행",
    )
    parser.add_argument(
        "--reset-state",
        action="store_true",
        help="state.json 초기화 후 처음부터 다시 수집",
    )
    args = parser.parse_args()

    if args.reset_state and STATE_FILE.exists():
        STATE_FILE.unlink()
        log.info("state.json 초기화 완료")

    if args.once:
        run_crawl()
    else:
        run_scheduler()

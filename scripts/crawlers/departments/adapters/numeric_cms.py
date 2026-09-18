"""Parser for the numeric-menu PKNU CMS originally used by CE."""

from __future__ import annotations

import hashlib
import re
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse

from bs4 import BeautifulSoup

from scripts.crawlers.departments.adapters.base import DepartmentCMSAdapter
from scripts.crawlers.departments.probe import numeric_menu_links, select_content_container
from scripts.crawlers.departments.urls import resolve_link


_NAV_TEXTS = {"목록보기", "다음", "이전", "next", "prev"}
_NAV_PHRASE_RE = re.compile(
    r"(다음\s*게시글이?\s*없습니다\.?|이전\s*게시글이?\s*없습니다\.?)", re.IGNORECASE,
)


def _text(element) -> str:
    return element.get_text(" ", strip=True) if element else ""


def _slug(url: str, extra: str = "") -> str:
    return hashlib.md5(f"{url}|{extra}".encode()).hexdigest()[:12]


def _detail_source_id(href: str, board_url: str) -> str:
    absolute = resolve_link(href, board_url)
    if not absolute:
        return ""
    parsed = urlparse(absolute)
    if parse_qs(parsed.query).get("action", [""])[0].lower() != "view":
        return ""
    source_id = str(parse_qs(parsed.query).get("no", [""])[0]).strip()
    return source_id if re.fullmatch(r"[A-Za-z0-9_-]+", source_id) else ""


def _list_date(container) -> str:
    date_element = container.select_one(".bdlDate, .date, [class*='date']")
    text = _text(date_element) if date_element else _text(container)
    match = re.search(r"20\d{2}[-./]\d{1,2}[-./]\d{1,2}", text)
    return match.group().replace(".", "-").replace("/", "-") if match else ""


def _fallback_list_items(soup: BeautifulSoup, board_url: str) -> list[dict[str, Any]]:
    """Parse newer numeric CMS table, card, and gallery list variants."""
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for link in soup.find_all("a", href=True):
        href = str(link.get("href") or "").strip()
        source_id = _detail_source_id(href, board_url)
        if not source_id or source_id in seen:
            continue
        if link.find_parent(class_=re.compile(r"(?:paging|pagination|board-nav|bdvNav)", re.I)):
            continue
        container = link.find_parent(["tr", "li"])
        if container is None:
            continue
        if container.name == "tr" and not container.select("td"):
            continue
        title = _text(link)
        if not title or title.strip().lower() in _NAV_TEXTS:
            continue

        number = ""
        if container.name == "tr":
            number_element = container.select_one(".bdlNum, .num, td:first-child")
            number = _text(number_element)
        marker = number.strip().upper()
        notice_badge = container.select_one('img[alt*="공지"], img[title*="공지"], em')
        badge_text = _text(notice_badge).strip().upper()
        notice = (
            marker in {"NOTICE", "N", "공지"}
            or badge_text in {"NOTICE", "N", "공지"}
        )
        post_no = int(marker) if marker.isdigit() else None
        seen.add(source_id)
        items.append({
            "source_id": source_id,
            "post_url": resolve_link(href, board_url),
            "num": "NOTICE" if notice else number,
            "post_no": post_no,
            "is_notice": notice,
            "date": _list_date(container),
            "title": title,
        })
    return items


def extract_bbs_id(html: str, page_url: str) -> str | None:
    soup = BeautifulSoup(html, "lxml")
    field = soup.select_one('input[name="bbsId"], input[name="bbs_id"]')
    if field and str(field.get("value") or "").isdigit():
        return str(field.get("value"))
    for pattern in (
        r"(?i)bbsId\s*[=:]\s*['\"]?(\d+)", r"(?i)bbs_id\s*[=:]\s*['\"]?(\d+)",
        r"(?i)[?&]bbsId=(\d+)",
    ):
        match = re.search(pattern, html)
        if match:
            return match.group(1)
    for anchor in soup.select("a[href]"):
        match = re.search(r"(?i)[?&](?:bbsId|bbs_id)=(\d+)", str(anchor.get("href") or ""))
        if match:
            return match.group(1)
    match = re.search(r"(?i)[?&]bbsId=(\d+)", page_url)
    return match.group(1) if match else None


def _has_board_table(soup: BeautifulSoup) -> bool:
    """Find structural board evidence without treating every view link as a board."""
    if soup.select_one(".a_brdList"):
        return True
    for table in soup.select("table"):
        headings = " ".join(_text(cell) for cell in table.select("thead th, tr:first-child th"))
        lowered = headings.lower()
        marker_count = sum(marker in lowered for marker in ("번호", "제목", "작성자", "작성일", "조회"))
        marker_count += sum(
            bool(re.search(rf"\b{marker}\b", lowered))
            for marker in ("no", "title", "author", "date", "views")
        )
        if marker_count >= 2 and table.select_one('a[href*="action=view"]'):
            return True
    return False


def _table_text(table) -> str:
    """Keep row/cell boundaries and carry merged row labels into each row."""
    lines = []
    spans: dict[int, tuple[str, int]] = {}
    caption = table.find("caption", recursive=False)
    if caption:
        lines.append(_text(caption))
    for row in table.find_all("tr"):
        if row.find_parent("table") is not table:
            continue
        values = {col: value for col, (value, _) in spans.items()}
        spans = {col: (value, left - 1) for col, (value, left) in spans.items() if left > 1}
        col = 0
        for cell in row.find_all(["th", "td"], recursive=False):
            while col in values:
                col += 1
            def span(name: str) -> int:
                try:
                    return min(1000, max(1, int(cell.get(name, 1))))
                except (TypeError, ValueError):
                    return 1
            width, height = span("colspan"), span("rowspan")
            value = _text(cell)
            for offset in range(width):
                text = value
                values[col + offset] = text
                if height > 1:
                    spans[col + offset] = (text, height - 1)
            col += width
        if any(values.values()):
            lines.append(" | ".join(values.get(i, "") for i in range(max(values) + 1)))
    return "\n".join(lines)


def extract_body_content(content_el) -> str:
    if content_el is None:
        return ""
    copy = BeautifulSoup(str(content_el), "lxml")
    for selector in ("script", "style", "#subTitle", ".c_bdvBtn", ".c_bdvNav", ".a_bdPaging", ".board-nav", ".btn-list"):
        for element in copy.select(selector):
            element.decompose()
    body = copy.select_one(".bdvEdit")
    if not body:
        for anchor in copy.select("a"):
            if anchor.get_text(strip=True).lower() in _NAV_TEXTS:
                anchor.decompose()
    root = body if body is not None else copy
    # Static guides often consist entirely of a table. Never discard a table
    # merely because it is the first one; the detail body already excludes metadata.
    tables = root.select("table")
    for table in reversed(tables):
        table.replace_with("\n" + _table_text(table) + "\n")
    text = root.get_text(" ", strip=not bool(tables))
    if tables:
        text = "\n".join(line.strip() for line in text.splitlines() if line.strip())
    text = _NAV_PHRASE_RE.sub("", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _content_root(soup):
    # select_one with a comma-separated selector uses document order, not
    # selector priority. CE has an empty layout .container before #sbCont.
    return soup.select_one("#sbCont") or select_content_container(soup)


def _empty_content_info(root, text, attachments):
    if text.strip():
        return {}
    images = [str(img.get("src")) for img in root.select("img[src]")] if root else []
    return {"content_state": "attachment_only" if attachments else "image_only" if images else "empty",
            "content_images": images}


def _attachment_candidate(href: str, name: str) -> bool:
    href_l, name_l = href.lower(), name.lower()
    blocked = (".html", ".htm", ".shtml", ".php", ".asp", ".aspx", ".jsp")
    if href_l.endswith(blocked) or name_l.endswith(blocked):
        return False
    extensions = r"\.(pdf|hwp|hwpx|doc|docx|xls|xlsx|ppt|pptx|zip|rar|7z|txt|csv|png|jpg|jpeg|gif)$"
    if re.search(extensions, href_l) or re.search(extensions, name_l):
        return True
    return any(token in href_l for token in ("download", "down", "attach", "file", "atchfile"))


class NumericCMSAdapter(DepartmentCMSAdapter):
    uses_numeric_post_order = True
    name = "numeric_cms"

    def discover_menus(self, html: str, base_url: str) -> list[dict[str, str]]:
        return numeric_menu_links(html, base_url)

    def analyze_section(self, *, name: str, page_url: str, html: str, final_url: str | None = None) -> dict[str, Any]:
        soup = BeautifulSoup(html, "lxml")
        parsed = urlparse(page_url)
        menu_id = parsed.path.rstrip("/").rsplit("/", 1)[-1]
        effective_url = final_url or page_url
        effective = urlparse(effective_url)
        common = {
            "id": f"menu_{menu_id}", "name": name or f"menu-{menu_id}",
            "category": "미분류", "path": parsed.path.rstrip("/"), "final_url": effective_url,
        }
        if effective.hostname and effective.hostname.lower() != (parsed.hostname or "").lower():
            return {**common, "kind": "static_page", "document_type": "guide", "bbs_id": None,
                    "confidence": 1.0, "status": "unsupported", "warnings": [f"external redirect: {effective_url}"]}
        if menu_id in {"1", "9999"} or name.strip().upper() in {"HOME", "SITEMAP"}:
            return {**common, "kind": "static_page", "document_type": "guide", "bbs_id": None,
                    "confidence": 1.0, "status": "unsupported", "warnings": ["navigation-only menu"]}
        bbs_id = extract_bbs_id(html, page_url)
        board_list = _has_board_table(soup)
        view_links = any("action=view" in str(a.get("href") or "").lower() for a in soup.select("a[href]"))
        is_board = board_list or bbs_id is not None
        has_content = select_content_container(soup) is not None
        needs_adapter = board_list and bbs_id is None
        static_collection = view_links and not is_board
        warnings = (
            ["board structure found but bbs_id is unavailable; adapter required"]
            if needs_adapter else []
        )
        confidence = (
            0.95 if board_list and bbs_id else
            0.85 if needs_adapter else
            0.8 if static_collection else
            0.65 if has_content else 0.2
        )
        status = (
            "requires_adapter" if needs_adapter else
            "candidate" if is_board or has_content or static_collection else
            "unsupported"
        )
        return {**common, "kind": "board" if is_board else "static_page",
                "document_type": "notice" if is_board else "guide", "bbs_id": bbs_id,
                "confidence": confidence, "status": status,
                "warnings": warnings}

    def parse_list(self, soup: BeautifulSoup, board_url: str) -> list[dict[str, Any]]:
        items = []
        for row in soup.select(".a_brdList tr"):
            cells, link = row.select("td"), row.select_one("td a[href]")
            if not cells or not link:
                continue
            number = cells[0].get_text(strip=True)
            notice = number.upper() == "NOTICE"
            try:
                post_no = None if notice else int(number)
            except ValueError:
                post_no = None
            post_url = resolve_link(link.get("href", ""), board_url)
            if not post_url:
                continue
            items.append({"post_url": post_url, "num": number,
                          "post_no": post_no, "is_notice": notice,
                          "date": cells[-2].get_text(strip=True) if len(cells) >= 2 else ""})
        return items or _fallback_list_items(soup, board_url)

    def parse_attachments(self, content, *, page_url: str, base_url: str, site_prefix: str) -> list[dict[str, str]]:
        attachments = []
        if not content:
            return attachments
        base = urlparse(base_url)
        for anchor in content.select("a[href]"):
            href, name = str(anchor.get("href") or ""), anchor.get_text(strip=True)
            if not href or href.startswith("javascript") or name.lower() in _NAV_TEXTS:
                continue
            absolute = resolve_link(href, page_url or base_url)
            if not absolute:
                continue
            parsed = urlparse(absolute)
            if "action=view" in parsed.query:
                continue
            if parsed.netloc == base.netloc and re.fullmatch(rf"/{re.escape(site_prefix)}/\d+", parsed.path):
                continue
            if _attachment_candidate(href, name):
                attachments.append({"name": name, "url": absolute})
        return attachments

    def parse_detail(self, soup: BeautifulSoup, post_url: str, item: dict[str, Any], *, base_url: str, site_prefix: str) -> dict[str, Any] | None:
        # Department boards can redirect individual posts to the university CMS.
        # Only accept its explicit detail structure on the known university host.
        if urlparse(post_url).hostname in {"www.pknu.ac.kr", "pknujob.pknu.ac.kr"}:
            requested_no = parse_qs(urlparse(str(item.get("post_url") or "")).query).get("no")
            actual_query = parse_qs(urlparse(post_url).query)
            if actual_query.get("action") != ["view"] or (requested_no and actual_query.get("no") != requested_no):
                return None
            title = soup.select_one("td.title_b")
            body = soup.select_one("div.bdvTxt")
            wrapper = soup.select_one("div.bdCont, form#frmPost")
            if title is None or body is None or wrapper is None:
                return None
            author = soup.select_one("td.text_l.noti_name")
            dates = re.findall(r"\d{4}-\d{2}-\d{2}", " ".join(_text(row) for row in wrapper.select("tr.noti")))
            attachments = [{"name": _text(a), "url": urljoin(post_url, a["href"])}
                           for a in wrapper.select('a[href*="boardDownload.do"]')]
            attachments = list({a["url"]: a for a in attachments}.values())
            text = extract_body_content(body)
            return {"title": _text(title), "author": _text(author), "date": dates[0] if dates else item.get("date", ""),
                    "url": post_url, "body": text, "is_notice": item.get("is_notice", False),
                    "attachments": attachments, **_empty_content_info(body, text, attachments)}
        title_el, content_el = soup.select_one(".bdvTitle"), soup.select_one(".a_bdCont")
        if not title_el and not content_el:
            return None
        title = _text(title_el)
        date = item.get("date", "")
        if content_el and not date:
            match = re.search(r"\d{4}-\d{2}-\d{2}", _text(content_el))
            date = match.group() if match else date
        body = extract_body_content(content_el)
        attachments = self.parse_attachments(content_el, page_url=post_url, base_url=base_url,
                                             site_prefix=site_prefix)
        root = content_el.select_one(".bdvEdit") if content_el else None
        return {"slug": _slug(post_url, title), "title": title, "date": date, "url": post_url,
                "is_notice": item.get("is_notice", False), "body": body, "attachments": attachments,
                **_empty_content_info(root or content_el, body, attachments)}

    def parse_static(self, soup: BeautifulSoup, *, fallback_title: str) -> dict[str, Any]:
        breadcrumb = soup.select(".a_sbtNav dd")
        title = breadcrumb[-1].get_text(strip=True) if breadcrumb else fallback_title
        title = title or _text(soup.select_one("title")) or fallback_title
        content = _content_root(soup)
        text = extract_body_content(content)
        attachments = self.parse_attachments(content, page_url="", base_url="", site_prefix="")
        return {"title": title, "content": text, "attachments": attachments,
                **_empty_content_info(content, text, attachments)}

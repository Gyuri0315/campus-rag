"""Crawl Pukyong National University rule pages.

This crawler targets the newer rule site at:
    https://www.pknu.ac.kr/rule/main.do

It writes documents under the rule data root:
    files/rule/output/json/pknu_rule_law/*.json
    files/rule/output/json/pknu_rule_bylaw/*.json
    files/rule/output/html/pknu_rule_law/*.html
    files/rule/output/html/pknu_rule_bylaw/*.html
    files/rule/output/files/... unless --no-download-files is used
"""

from __future__ import annotations

import argparse
import html
import hashlib
import json
import logging
import re
import ssl
import sys
import time
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, unquote, urlencode, urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter

BASE_URL = "https://www.pknu.ac.kr"
RULE_URL = f"{BASE_URL}/rule"
LAW_URL = "https://www.law.go.kr"

PROJECT_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_JSON = PROJECT_ROOT / "files" / "rule" / "output" / "json"
OUTPUT_HTML = PROJECT_ROOT / "files" / "rule" / "output" / "html"
OUTPUT_FILES = PROJECT_ROOT / "files" / "rule" / "output" / "files"
OUTPUT_TREE = PROJECT_ROOT / "files" / "rule" / "output" / "tree"
LOG_DIR = PROJECT_ROOT / "logs"
LOG_FILE = LOG_DIR / "rule_crawler.log"

REQUEST_TIMEOUT = 30
REQUEST_DELAY = 0.25

DOWNLOAD_PATH_MARKERS = (
    "download",
    "ruleBoardDownload.do",
    "flDownload.do",
    "fileDown",
    "fileDownload",
    "atchFile",
)
FILE_EXTENSIONS = {
    ".pdf",
    ".hwp",
    ".hwpx",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
    ".zip",
    ".rar",
    ".7z",
    ".txt",
    ".csv",
}
STATIC_EXTENSIONS = {
    ".css",
    ".js",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".svg",
    ".ico",
    ".woff",
    ".woff2",
    ".ttf",
    ".eot",
}


class PknuSslAdapter(HTTPAdapter):
    """Allow the current PKNU rule server TLS configuration with OpenSSL 3."""

    def init_poolmanager(self, *args: Any, **kwargs: Any) -> None:
        context = ssl.create_default_context()
        context.set_ciphers("DEFAULT@SECLEVEL=1")
        kwargs["ssl_context"] = context
        return super().init_poolmanager(*args, **kwargs)


GENERIC_ATTACHMENT_NAMES = {
    "attachment",
    "download",
    "file",
    "hwp파일 다운로드",
    "pdf파일 다운로드",
    "파일 다운로드",
}

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class LawNode:
    lid: str
    title: str
    kind_type: str
    issue: str
    effective: str
    law_path: str
    parent_lid: str
    parent_title: str


@dataclass(frozen=True)
class BylawItem:
    bbs_id: str
    board_seq: str
    title: str
    list_url: str
    page_index: int


def configure_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(LOG_FILE, encoding="utf-8"),
        ],
    )


def build_session() -> requests.Session:
    session = requests.Session()
    session.mount(BASE_URL, PknuSslAdapter())
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8",
            "Referer": f"{RULE_URL}/main.do",
        }
    )
    return session


def fetch(session: requests.Session, url: str, **kwargs: Any) -> requests.Response:
    resp = session.get(url, timeout=REQUEST_TIMEOUT, **kwargs)
    resp.raise_for_status()
    content_type = resp.headers.get("Content-Type", "").lower()
    host = urlsplit(resp.url or url).netloc.lower()
    if "pknu.ac.kr" in host and ("text/" in content_type or "json" in content_type or not content_type):
        try:
            resp.content.decode("utf-8")
            resp.encoding = "utf-8"
        except UnicodeDecodeError:
            resp.encoding = resp.apparent_encoding or "cp949"
    elif "charset=utf-8" in content_type:
        resp.encoding = "utf-8"
    elif resp.encoding is None or resp.encoding.lower() == "iso-8859-1":
        resp.encoding = resp.apparent_encoding or "utf-8"
    time.sleep(REQUEST_DELAY)
    return resp


def normalize_text(text: str) -> str:
    text = html.unescape(text or "").replace("\xa0", " ")
    return " ".join(text.split()).strip()


def html_to_text(fragment: str) -> str:
    soup = BeautifulSoup(fragment or "", "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "canvas", "form"]):
        tag.decompose()
    return normalize_text(soup.get_text("\n", strip=True))


def slug_for(*parts: str) -> str:
    raw = "|".join(parts)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:12]


def sanitize_filename(filename: str) -> str:
    filename = Path(filename).name
    filename = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", filename)
    return filename.strip(" .") or "attachment"


def repair_mojibake(text: str) -> str:
    if not text:
        return text
    try:
        repaired = text.encode("latin1").decode("utf-8")
    except UnicodeError:
        return text
    hangul_before = len(re.findall(r"[\uac00-\ud7a3]", text))
    hangul_after = len(re.findall(r"[\uac00-\ud7a3]", repaired))
    return repaired if hangul_after > hangul_before else text


def is_generic_attachment_name(name: str) -> bool:
    normalized = normalize_text(Path(name).stem or name).lower()
    return normalized in GENERIC_ATTACHMENT_NAMES


def is_mojibake_filename(name: str) -> bool:
    return bool(re.search(r"[ÃÂ]|[ðÐ]|[ëìêí][\x80-\xff\u0080-\u00ff]?", name)) and not re.search(
        r"[\uac00-\ud7a3]", name
    )


def dedupe_join_texts(*texts: str) -> str:
    merged: list[str] = []
    seen: set[str] = set()
    for text in texts:
        normalized = normalize_text(text)
        if not normalized or normalized in seen:
            continue
        merged.append(normalized)
        seen.add(normalized)
    return "\n\n".join(merged)


def save_document(doc: dict[str, Any], raw_html: str, category: str) -> None:
    slug = doc["slug"]
    json_dir = OUTPUT_JSON / category
    html_dir = OUTPUT_HTML / category
    json_dir.mkdir(parents=True, exist_ok=True)
    html_dir.mkdir(parents=True, exist_ok=True)
    (json_dir / f"{slug}.json").write_text(
        json.dumps(doc, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (html_dir / f"{slug}.html").write_text(raw_html or "", encoding="utf-8")


def filename_from_response(resp: requests.Response, fallback: str) -> str:
    disposition = resp.headers.get("Content-Disposition", "")
    for pattern in (r"filename\*=UTF-8''([^;]+)", r'filename="([^"]+)"', r"filename=([^;]+)"):
        match = re.search(pattern, disposition, flags=re.I)
        if match:
            return sanitize_filename(repair_mojibake(unquote(match.group(1).strip())))
    return sanitize_filename(fallback)


def filename_from_url(url: str, fallback: str = "attachment") -> str:
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    for key in ("filename", "fileName", "fileNm", "flNm", "orgFileNm", "downFileName"):
        value = query.get(key)
        if value:
            return sanitize_filename(unquote(value))
    path_name = unquote(Path(parts.path).name)
    return sanitize_filename(path_name or fallback)


def select_download_filename(resp: requests.Response, url: str, fallback: str) -> str:
    response_name = filename_from_response(resp, fallback)
    url_name = filename_from_url(url, fallback)
    fallback_name = sanitize_filename(fallback)

    if not is_generic_attachment_name(url_name) and (
        is_generic_attachment_name(fallback_name)
        or is_generic_attachment_name(response_name)
        or is_mojibake_filename(response_name)
    ):
        return url_name
    return response_name


def detect_download_extension(path: Path) -> str:
    try:
        head = path.read_bytes()[:4096]
    except Exception:
        return ""
    lowered = head.lower()
    if head.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        return ".hwp"
    if head.startswith(b"PK\x03\x04"):
        if b"application/hwp+zip" in lowered or b"mimetypeapplication/hwp+zip" in lowered:
            return ".hwpx"
        try:
            with zipfile.ZipFile(path) as zf:
                names = set(zf.namelist())
        except zipfile.BadZipFile:
            names = set()
        if any(name.startswith("word/") for name in names):
            return ".docx"
        if any(name.startswith("xl/") for name in names):
            return ".xlsx"
        if any(name.startswith("ppt/") for name in names):
            return ".pptx"
        return ".zip"
    if head.startswith(b"%PDF"):
        return ".pdf"
    return ""


def unique_path(path: Path, reserved_names: set[str] | None = None) -> Path:
    reserved_names = reserved_names or set()
    if path.name not in reserved_names:
        return path
    stem = path.stem
    suffix = path.suffix
    for index in range(2, 10_000):
        candidate = path.with_name(f"{stem}-{index}{suffix}")
        if candidate.name not in reserved_names:
            return candidate
    raise RuntimeError(f"Could not find unique filename for {path}")


def is_probable_download_url(url: str) -> bool:
    parts = urlsplit(url)
    lowered = unquote((parts.path + "?" + parts.query)).lower()
    path_ext = Path(parts.path).suffix.lower()
    return path_ext in FILE_EXTENSIONS or any(marker.lower() in lowered for marker in DOWNLOAD_PATH_MARKERS)


def is_static_resource_url(url: str) -> bool:
    return Path(urlsplit(url).path).suffix.lower() in STATIC_EXTENSIONS


def attachment_identity(attachment: dict[str, str]) -> tuple[str, str]:
    return (attachment.get("url", ""), attachment.get("name", ""))


def add_attachment(
    attachments: list[dict[str, str]],
    seen: set[tuple[str, str]],
    name: str,
    url: str,
    source_page_url: str,
    source_site: str,
) -> None:
    name = normalize_text(name) or filename_from_url(url)
    item = {
        "name": name,
        "url": url,
        "source_page_url": source_page_url,
        "source_site": source_site,
    }
    key = attachment_identity(item)
    if key in seen:
        return
    seen.add(key)
    attachments.append(item)


def extract_download_urls_from_text(text: str, base_url: str) -> list[str]:
    urls: list[str] = []
    for match in re.finditer(r"""['"]([^'"]*(?:download|Download|ruleBoardDownload|flDownload)[^'"]*)['"]""", text):
        raw = html.unescape(match.group(1)).strip()
        if not raw or raw.startswith(("javascript:", "#")):
            continue
        urls.append(urljoin(base_url, raw))
    return urls


def collect_download_links(
    root: BeautifulSoup,
    base_url: str,
    source_page_url: str,
    source_site: str,
) -> list[dict[str, str]]:
    attachments: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for tag in root.find_all(["a", "area"], href=True):
        href = str(tag.get("href") or "").strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        url = urljoin(base_url, href)
        if not is_probable_download_url(url):
            continue
        img = tag.find("img")
        name = normalize_text(
            str(tag.get("title") or tag.get("download") or "")
            or tag.get_text(" ", strip=True)
            or (img.get("alt", "") if img else "")
            or filename_from_url(url)
        )
        add_attachment(attachments, seen, name, url, source_page_url, source_site)
    for tag in root.find_all(True):
        scriptish = " ".join(str(tag.get(attr) or "") for attr in ("onclick", "data-url", "data-href", "data-download-url"))
        for url in extract_download_urls_from_text(scriptish, base_url):
            if not is_probable_download_url(url):
                continue
            name = normalize_text(str(tag.get("title") or tag.get("download") or "") or tag.get_text(" ", strip=True))
            add_attachment(attachments, seen, name or filename_from_url(url), url, source_page_url, source_site)
    return attachments


def collect_preview_resources(root: BeautifulSoup, base_url: str) -> list[dict[str, str]]:
    resources: list[dict[str, str]] = []
    seen: set[str] = set()
    for tag in root.find_all(["iframe", "frame", "embed", "object"]):
        raw = tag.get("src") or tag.get("data")
        if not raw:
            continue
        url = urljoin(base_url, str(raw))
        if url in seen or is_static_resource_url(url) or is_probable_download_url(url):
            continue
        seen.add(url)
        title = normalize_text(str(tag.get("title") or tag.get("name") or tag.get("id") or "preview"))
        resources.append({"title": title or "preview", "url": url})
    return resources


def download_attachment(
    session: requests.Session,
    url: str,
    category: str,
    slug: str,
    fallback_name: str,
    reserved_names: set[str] | None = None,
) -> dict[str, str]:
    resp = fetch(session, url, stream=True)
    filename = select_download_filename(resp, url, fallback_name)
    if not Path(filename).suffix:
        path_ext = Path(urlsplit(url).path).suffix
        if path_ext:
            filename += path_ext

    file_dir = OUTPUT_FILES / category / slug
    file_dir.mkdir(parents=True, exist_ok=True)
    target = unique_path(file_dir / filename, reserved_names)
    if reserved_names is not None:
        reserved_names.add(target.name)
    with target.open("wb") as handle:
        for chunk in resp.iter_content(chunk_size=1024 * 256):
            if chunk:
                handle.write(chunk)

    detected_ext = detect_download_extension(target)
    if detected_ext and target.suffix.lower() != detected_ext:
        renamed = unique_path(target.with_suffix(detected_ext), reserved_names)
        target.replace(renamed)
        if reserved_names is not None:
            reserved_names.discard(target.name)
            reserved_names.add(renamed.name)
        target = renamed

    return {
        "saved_name": target.name,
        "saved_path": target.as_posix(),
        "content_type": resp.headers.get("Content-Type", ""),
        "downloaded_from_url": url,
    }


def extract_preview_texts(
    session: requests.Session,
    resources: list[dict[str, str]],
    referer: str,
) -> list[dict[str, str]]:
    previews: list[dict[str, str]] = []
    for resource in resources:
        url = resource.get("url", "")
        if not url:
            continue
        preview = {
            "title": resource.get("title", "preview"),
            "url": url,
            "text": "",
            "content_type": "",
        }
        try:
            resp = fetch(session, url, headers={"Referer": referer})
            content_type = resp.headers.get("Content-Type", "")
            preview["content_type"] = content_type
            if "text/" in content_type.lower() or "html" in content_type.lower() or "xml" in content_type.lower():
                preview["text"] = html_to_text(resp.text)
            else:
                preview["error"] = f"Skipped non-text preview content: {content_type}"
        except Exception as exc:
            preview["error"] = str(exc)
            log.warning("Preview text extraction failed: %s (%s)", url, exc)
        previews.append(preview)
    return previews


def parse_tree_payload(raw: dict[str, Any]) -> dict[str, Any]:
    tree_data = raw.get("datas", {}).get("treeData", raw)
    if isinstance(tree_data, str):
        tree_data = json.loads(html.unescape(tree_data))
    if "mixedNodes" not in tree_data and "ruleTree" in tree_data:
        tree_data["mixedNodes"] = tree_data["ruleTree"]
    return tree_data


def node_title(node: dict[str, Any]) -> str:
    data = node.get("data") or {}
    return normalize_text(data.get("name") or node.get("text") or "")


def normalize_tree_nodes(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {str(node.get("id")): node for node in nodes if node.get("id")}

    def path_for(node_id: str) -> list[dict[str, str]]:
        path: list[dict[str, str]] = []
        seen: set[str] = set()
        current = by_id.get(node_id)
        while current:
            current_id = str(current.get("id") or "")
            if not current_id or current_id in seen:
                break
            seen.add(current_id)
            data = current.get("data") or {}
            path.append(
                {
                    "id": current_id,
                    "lid": str(data.get("lid") or ""),
                    "title": node_title(current),
                    "kind_type": str(data.get("kindType") or current.get("type") or ""),
                }
            )
            parent_id = str(current.get("parent") or "")
            current = by_id.get(parent_id)
        return list(reversed(path))

    normalized: list[dict[str, Any]] = []
    for node in nodes:
        node_id = str(node.get("id") or "")
        if not node_id:
            continue
        data = node.get("data") or {}
        parent_id = str(node.get("parent") or "")
        parent_node = by_id.get(parent_id) or {}
        parent_data = parent_node.get("data") or {}
        path = path_for(node_id)
        kind_type = str(data.get("kindType") or node.get("type") or "")
        normalized.append(
            {
                "id": node_id,
                "parent_id": parent_id,
                "title": node_title(node),
                "kind_type": kind_type,
                "lid": str(data.get("lid") or ""),
                "parent_lid": str(parent_data.get("lid") or ""),
                "parent_title": node_title(parent_node) if parent_node else "",
                "issue": str(data.get("issue") or ""),
                "effective": str(data.get("eff") or ""),
                "law_path": html.unescape(str(data.get("url") or "")),
                "depth": max(0, len(path) - 1),
                "path_ids": [item["id"] for item in path],
                "path_lids": [item["lid"] for item in path if item["lid"]],
                "path_titles": [item["title"] for item in path if item["title"]],
                "is_law_crawl_target": kind_type in {"hak", "gyu"},
            }
        )
    return normalized


def save_rule_tree(raw_payload: dict[str, Any], tree: dict[str, Any]) -> None:
    nodes = tree.get("mixedNodes") or []
    if not isinstance(nodes, list):
        log.warning("Could not save rule tree; mixedNodes is not a list")
        return

    OUTPUT_TREE.mkdir(parents=True, exist_ok=True)
    normalized = normalize_tree_nodes(nodes)
    fetched_at = datetime.now().isoformat(timespec="seconds")

    (OUTPUT_TREE / "rule_tree_raw.json").write_text(
        json.dumps(
            {
                "fetched_at": fetched_at,
                "source_url": f"{RULE_URL}/loadTree.do",
                "payload": raw_payload,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (OUTPUT_TREE / "rule_tree_nodes.json").write_text(
        json.dumps(
            {
                "fetched_at": fetched_at,
                "source_url": f"{RULE_URL}/loadTree.do",
                "num_nodes": len(normalized),
                "num_law_crawl_targets": sum(1 for node in normalized if node["is_law_crawl_target"]),
                "nodes": normalized,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    log.info("Saved rule tree nodes=%d to %s", len(normalized), OUTPUT_TREE)


def load_law_nodes(session: requests.Session, save_tree: bool = True) -> list[LawNode]:
    payload = fetch(session, f"{RULE_URL}/loadTree.do").json()
    tree = parse_tree_payload(payload)
    nodes = tree.get("mixedNodes") or []
    if save_tree:
        save_rule_tree(payload, tree)
    by_id = {str(node.get("id")): node for node in nodes if node.get("id")}

    law_nodes: list[LawNode] = []
    for node in nodes:
        data = node.get("data") or {}
        kind_type = str(data.get("kindType") or node.get("type") or "")
        if kind_type not in {"hak", "gyu"}:
            continue

        parent_id = str(node.get("parent") or "")
        parent_node = by_id.get(parent_id) or {}
        parent_data = parent_node.get("data") or {}
        law_nodes.append(
            LawNode(
                lid=str(data.get("lid") or "").strip(),
                title=normalize_text(data.get("name") or node.get("text") or ""),
                kind_type=kind_type,
                issue=str(data.get("issue") or ""),
                effective=str(data.get("eff") or ""),
                law_path=html.unescape(str(data.get("url") or "")),
                parent_lid=str(parent_data.get("lid") or ""),
                parent_title=normalize_text(parent_data.get("name") or parent_node.get("text") or ""),
            )
        )
    return [node for node in law_nodes if node.lid and node.law_path]


def with_query_param(url: str, **params: str) -> str:
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query.update(params)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def find_lsw_url(session: requests.Session, law_path: str) -> str | None:
    wrapper_url = urljoin(LAW_URL, law_path)
    wrapper_url = with_query_param(wrapper_url, type="HTML")
    resp = fetch(session, wrapper_url, headers={"Referer": f"{RULE_URL}/schRegAndRuleList.do"})
    soup = BeautifulSoup(resp.text, "html.parser")

    iframe = soup.find("iframe", src=True)
    if iframe:
        return urljoin(LAW_URL, str(iframe["src"]))

    hidden = soup.find("input", {"id": "url"})
    if hidden and hidden.get("value"):
        return str(hidden["value"])
    return None


def extract_ajax_params(lsw_html: str) -> dict[str, str]:
    soup = BeautifulSoup(lsw_html, "html.parser")
    params = {
        "schlPubRulSeq": "",
        "joTpYn": "Y",
        "languageType": "KO",
        "chrClsCd": "010202",
        "urlMode": "schlPubRulLsInfoP",
        "schlPubRulId": "",
        "prmlNo": "",
        "prmlYd": "",
        "efYd": "",
        "preview": "",
    }

    for key, input_id in {
        "schlPubRulSeq": "schlPubRulSeq",
        "schlPubRulId": "lsId",
        "prmlNo": "prmlNo",
        "prmlYd": "prmlYd",
        "efYd": "efYd",
    }.items():
        tag = soup.find("input", {"id": input_id})
        if tag and tag.get("value") is not None:
            params[key] = str(tag["value"])

    script_text = "\n".join(script.get_text("\n") for script in soup.find_all("script"))
    for key in params:
        if params[key]:
            continue
        match = re.search(rf'{re.escape(key)}\s*:\s*"?([^",\n]+)"?', script_text)
        if match:
            params[key] = match.group(1).strip()

    return params


def parse_law_ajax_html(raw_html: str) -> tuple[str, list[dict[str, str]], list[dict[str, str]]]:
    soup = BeautifulSoup(raw_html, "html.parser")
    root = soup.select_one("#conScroll") or soup.select_one("#contentBody") or soup.body or soup

    attachments = collect_download_links(root, LAW_URL + "/LSW/", LAW_URL, "law.go.kr")
    preview_resources = collect_preview_resources(root, LAW_URL + "/LSW/")

    for tag in root.select("input, script, style, noscript, svg, canvas, iframe, frame, embed, object"):
        tag.decompose()

    return normalize_text(root.get_text("\n", strip=True)), attachments, preview_resources


def crawl_law_node(
    session: requests.Session,
    node: LawNode,
    download_files: bool,
) -> dict[str, Any] | None:
    try:
        lsw_url = find_lsw_url(session, node.law_path)
        if not lsw_url:
            raise RuntimeError("Could not find law.go.kr LSW iframe URL")

        lsw_resp = fetch(session, lsw_url, headers={"Referer": f"{RULE_URL}/schRegAndRuleList.do"})
        params = extract_ajax_params(lsw_resp.text)
        ajax_url = f"{LAW_URL}/LSW/schlPubRulInfoR.do?{urlencode(params)}"
        ajax_resp = fetch(session, ajax_url, headers={"Referer": lsw_url})
        content, attachments, preview_resources = parse_law_ajax_html(ajax_resp.text)
        preview_texts = extract_preview_texts(session, preview_resources, ajax_url)
    except Exception as exc:
        log.warning("Law crawl failed for LID=%s title=%s: %s", node.lid, node.title, exc)
        return None

    slug = slug_for("law", node.lid, node.title)
    attachment_texts: list[dict[str, Any]] = []
    file_preview_texts: list[dict[str, Any]] = []
    if download_files:
        attachments = save_attachments(session, attachments, "pknu_rule_law", slug)
        attachment_texts = extract_attachment_texts(attachments)
        file_preview_texts = build_file_preview_texts(attachment_texts)
    combined_content = dedupe_join_texts(
        content,
        *(preview.get("text", "") for preview in preview_texts),
        *(preview.get("text", "") for preview in file_preview_texts),
    )

    doc = {
        "slug": slug,
        "title": node.title,
        "url": lsw_url,
        "category": "pknu_rule_law",
        "subcategory": "school_rule" if node.kind_type == "hak" else "regulation",
        "type": node.kind_type,
        "date": node.effective,
        "source_site": "pknu_rule/law.go.kr",
        "source_id": node.lid,
        "parent_lid": node.parent_lid,
        "parent_title": node.parent_title,
        "issued_at": node.issue,
        "effective_at": node.effective,
        "content": combined_content,
        "html_text": content,
        "html_text_source": "law_ajax_html",
        "page_content": content,
        "preview_texts": preview_texts,
        "attachments": attachments,
        "attachment_texts": attachment_texts,
        "file_preview_texts": file_preview_texts,
        "crawled_at": datetime.now().isoformat(timespec="seconds"),
    }
    save_document(doc, ajax_resp.text, "pknu_rule_law")
    return doc


def parse_last_page(soup: BeautifulSoup) -> int:
    last = 1
    for anchor in soup.select("ul.paging a[href]"):
        href = str(anchor["href"])
        match = re.search(r"pageIndex=(\d+)", href)
        if match:
            last = max(last, int(match.group(1)))
    return last


def parse_bylaw_list_page(html_text: str, list_url: str, page_index: int) -> tuple[list[BylawItem], int]:
    soup = BeautifulSoup(html_text, "html.parser")
    items: list[BylawItem] = []
    pattern = re.compile(r"onLoadPage\((\d+)\s*,\s*(\d+)\)")

    for anchor in soup.select("section.dtLaw_list a[href]"):
        href = str(anchor.get("href") or "")
        match = pattern.search(href)
        if not match:
            continue
        title = normalize_text(anchor.get("title") or anchor.get_text(" ", strip=True))
        items.append(
            BylawItem(
                bbs_id=match.group(1),
                board_seq=match.group(2),
                title=title,
                list_url=list_url,
                page_index=page_index,
            )
        )
    return items, parse_last_page(soup)


def load_bylaw_list_items(session: requests.Session, max_pages: int | None) -> list[BylawItem]:
    first_url = f"{RULE_URL}/bylawsAndGuidelineList.do?pageIndex=1"
    first = fetch(session, first_url)
    items, last_page = parse_bylaw_list_page(first.text, first_url, 1)
    if max_pages:
        last_page = min(last_page, max_pages)

    for page_index in range(2, last_page + 1):
        page_url = f"{RULE_URL}/bylawsAndGuidelineList.do?pageIndex={page_index}"
        resp = fetch(session, page_url)
        page_items, _ = parse_bylaw_list_page(resp.text, page_url, page_index)
        items.extend(page_items)
    return items


def save_attachments(
    session: requests.Session,
    attachments: list[dict[str, str]],
    category: str,
    slug: str,
) -> list[dict[str, str]]:
    saved: list[dict[str, str]] = []
    reserved_names: set[str] = set()
    for idx, attachment in enumerate(attachments, start=1):
        item = dict(attachment)
        try:
            fallback = item.get("name") or f"attachment-{idx}"
            item.update(download_attachment(session, item["url"], category, slug, fallback, reserved_names))
        except Exception as exc:
            item["download_error"] = str(exc)
            log.warning("Attachment download failed: %s (%s)", item.get("url"), exc)
        saved.append(item)
    return saved


def extract_attachment_texts(attachments: list[dict[str, str]]) -> list[dict[str, Any]]:
    previews: list[dict[str, Any]] = []
    try:
        from preprocessing import SUPPORTED_EXTS, extract_blocks
    except Exception as exc:
        reason = f"preprocessing.extract_blocks import failed: {exc}"
        for attachment in attachments:
            if attachment.get("saved_path"):
                attachment["extract_error"] = reason
        return previews

    for attachment in attachments:
        saved_path = attachment.get("saved_path")
        if not saved_path:
            continue

        path = Path(saved_path)
        preview: dict[str, Any] = {
            "name": attachment.get("name", ""),
            "saved_path": path.as_posix(),
            "url": attachment.get("url", ""),
            "text": "",
            "num_blocks": 0,
            "num_chars": 0,
        }
        try:
            if not path.exists():
                raise FileNotFoundError(path)
            if path.stat().st_size == 0:
                reason = "empty attachment file"
                preview["skipped"] = reason
                attachment["extract_skipped"] = reason
                previews.append(preview)
                continue
            if path.suffix.lower() not in SUPPORTED_EXTS:
                reason = f"unsupported extension: {path.suffix.lower() or '(none)'}"
                preview["skipped"] = reason
                attachment["extract_skipped"] = reason
                previews.append(preview)
                continue
            blocks = extract_blocks(path)
            text = dedupe_join_texts(*(str(block.get("text", "")) for block in blocks))
            preview.update({"text": text, "num_blocks": len(blocks), "num_chars": len(text)})
            attachment["extracted_text_chars"] = str(len(text))
        except Exception as exc:
            preview["error"] = str(exc)
            attachment["extract_error"] = str(exc)
            log.warning("Attachment text extraction failed: %s (%s)", saved_path, exc)
        previews.append(preview)
    return previews


def build_file_preview_texts(attachment_texts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    file_previews: list[dict[str, Any]] = []
    for attachment_text in attachment_texts:
        preview = dict(attachment_text)
        preview["source"] = "downloaded_attachment"
        preview["preview_equivalent"] = True
        file_previews.append(preview)
    return file_previews


def load_bylaw_preview_map(session: requests.Session) -> dict[str, dict[str, str]]:
    url = f"{RULE_URL}/byRows.do"
    try:
        payload = fetch(session, url, headers={"Referer": f"{RULE_URL}/main.do"}).json()
    except Exception as exc:
        log.warning("Could not load bylaw preview rows from %s: %s", url, exc)
        return {}

    preview_map: dict[str, dict[str, str]] = {}
    for row in payload.get("datas", []) or []:
        board_seq = str(row.get("boardSeq") or "").strip()
        if not board_seq:
            continue
        content_html = str(row.get("cont") or "")
        preview_map[board_seq] = {
            "title": normalize_text(str(row.get("title") or "")),
            "content": html_to_text(content_html),
            "content_html": content_html,
            "opn_at": str(row.get("opnAt") or ""),
            "point_text": normalize_text(str(row.get("pointText") or "")),
        }
    return preview_map


def parse_bylaw_detail(html_text: str, item: BylawItem, detail_url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html_text, "html.parser")
    title = normalize_text((soup.find("h4") or {}).get_text(" ", strip=True)) or item.title

    author = ""
    date = ""
    for meta_item in soup.select(".subTableDtl li"):
        text = normalize_text(meta_item.get_text(" ", strip=True))
        if "작성자" in text:
            author = normalize_text(text.replace("작성자", "", 1))
        if "작성일" in text:
            date_match = re.search(r"\d{4}-\d{2}-\d{2}", text)
            if date_match:
                date = date_match.group(0)

    detail_base_url = f"{RULE_URL}/"
    attachments = collect_download_links(soup, detail_base_url, detail_url, "pknu_rule")
    preview_resources = collect_preview_resources(soup, detail_base_url)

    content_root = soup.select_one(".subTableDtl > section") or soup.select_one(".subTableDtl") or soup.body or soup
    for tag in content_root.select("script, style, noscript, svg, canvas, form, iframe, frame, embed, object"):
        tag.decompose()

    return {
        "title": title,
        "author": author,
        "date": date,
        "content": normalize_text(content_root.get_text("\n", strip=True)),
        "content_html": str(content_root),
        "attachments": attachments,
        "preview_resources": preview_resources,
    }


def crawl_bylaw_item(
    session: requests.Session,
    item: BylawItem,
    download_files: bool,
    preview_entry: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    detail_url = f"{RULE_URL}/bylawsAndGuidelineView.do?bbsId={item.bbs_id}&no={item.board_seq}"
    try:
        resp = fetch(session, detail_url, headers={"Referer": item.list_url})
        detail = parse_bylaw_detail(resp.text, item, detail_url)
        preview_texts = extract_preview_texts(session, detail["preview_resources"], detail_url)
    except Exception as exc:
        log.warning("Bylaw crawl failed for no=%s title=%s: %s", item.board_seq, item.title, exc)
        return None

    slug = slug_for("bylaw", item.bbs_id, item.board_seq, detail["title"])
    attachments = detail["attachments"]
    attachment_texts: list[dict[str, Any]] = []
    file_preview_texts: list[dict[str, Any]] = []
    if download_files:
        attachments = save_attachments(session, attachments, "pknu_rule_bylaw", slug)
        attachment_texts = extract_attachment_texts(attachments)
        file_preview_texts = build_file_preview_texts(attachment_texts)

    preview_entry = preview_entry or {}
    preview_content = preview_entry.get("content", "")
    combined_content = dedupe_join_texts(
        detail["content"],
        preview_content,
        *(preview.get("text", "") for preview in preview_texts),
        *(preview.get("text", "") for preview in file_preview_texts),
    )

    doc = {
        "slug": slug,
        "title": detail["title"],
        "url": detail_url,
        "category": "pknu_rule_bylaw",
        "subcategory": "bylaw" if item.bbs_id == "1" else "guideline",
        "type": "bylaw_guideline",
        "date": detail["date"],
        "source_site": "pknu_rule",
        "source_id": item.board_seq,
        "bbs_id": item.bbs_id,
        "author": detail["author"],
        "content": combined_content,
        "html_text": detail["content"],
        "html_text_source": "pknu_detail_html",
        "page_content": detail["content"],
        "content_html": detail["content_html"],
        "preview_content": preview_content,
        "preview_content_html": preview_entry.get("content_html", ""),
        "preview_texts": preview_texts,
        "opn_at": preview_entry.get("opn_at", ""),
        "point_text": preview_entry.get("point_text", ""),
        "attachments": attachments,
        "attachment_texts": attachment_texts,
        "file_preview_texts": file_preview_texts,
        "crawled_at": datetime.now().isoformat(timespec="seconds"),
    }
    save_document(doc, resp.text, "pknu_rule_bylaw")
    return doc


def crawl_laws(
    session: requests.Session,
    max_items: int | None,
    download_files: bool,
    save_tree: bool,
) -> int:
    nodes = load_law_nodes(session, save_tree=save_tree)
    if max_items:
        nodes = nodes[:max_items]
    log.info("Found %s law/rule nodes", len(nodes))

    saved = 0
    for index, node in enumerate(nodes, start=1):
        log.info("Crawling law %s/%s LID=%s %s", index, len(nodes), node.lid, node.title)
        if crawl_law_node(session, node, download_files):
            saved += 1
    return saved


def crawl_bylaws(session: requests.Session, max_pages: int | None, download_files: bool) -> int:
    items = load_bylaw_list_items(session, max_pages)
    preview_map = load_bylaw_preview_map(session)
    log.info("Found %s bylaw/guideline list items", len(items))

    saved = 0
    for index, item in enumerate(items, start=1):
        log.info("Crawling bylaw %s/%s no=%s %s", index, len(items), item.board_seq, item.title)
        if crawl_bylaw_item(session, item, download_files, preview_map.get(item.board_seq)):
            saved += 1
    return saved


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Crawl PKN rule pages into files/rule/output.")
    parser.add_argument("--laws", action="store_true", help="Crawl school rules/regulations from the rule tree.")
    parser.add_argument("--bylaws", action="store_true", help="Crawl bylaws/guidelines list and detail pages.")
    parser.set_defaults(download_files=True)
    parser.set_defaults(save_tree=True)
    parser.add_argument(
        "--download-files",
        dest="download_files",
        action="store_true",
        help="Download referenced attachments (default).",
    )
    parser.add_argument(
        "--no-download-files",
        dest="download_files",
        action="store_false",
        help="Skip attachment downloads and attachment text extraction.",
    )
    parser.add_argument("--max-law-items", type=int, default=None, help="Limit law/rule items for smoke tests.")
    parser.add_argument("--max-bylaw-pages", type=int, default=None, help="Limit bylaw list pages for smoke tests.")
    parser.add_argument(
        "--save-tree",
        dest="save_tree",
        action="store_true",
        help="Save the raw and normalized rule tree when crawling --laws (default).",
    )
    parser.add_argument(
        "--no-save-tree",
        dest="save_tree",
        action="store_false",
        help="Do not save the rule tree payload while crawling --laws.",
    )
    return parser.parse_args()


def main() -> int:
    configure_logging()
    args = parse_args()
    if not args.laws and not args.bylaws:
        args.laws = True
        args.bylaws = True

    session = build_session()
    total = 0
    if args.laws:
        total += crawl_laws(session, args.max_law_items, args.download_files, args.save_tree)
    if args.bylaws:
        total += crawl_bylaws(session, args.max_bylaw_pages, args.download_files)

    log.info("Done. Saved %s documents.", total)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

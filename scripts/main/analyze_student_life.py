"""1단계: /main/434 + eBook 구조 분석 (임시 스크립트)"""
from __future__ import annotations

import re
import ssl
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter

PROJECT_ROOT = Path(__file__).resolve().parents[2]

BASE = "https://www.pknu.ac.kr"
GUIDE = f"{BASE}/main/434"
EBOOK = f"{BASE}/ebook/col_life/kor/index.html"
OUT = PROJECT_ROOT / "scripts" / "main" / "_student_life_analysis"
OUT.mkdir(parents=True, exist_ok=True)

EXCLUDE_KEYWORDS = ("대학생활계획서", "콘테스트", "우수작")


class LegacySSLAdapter(HTTPAdapter):
    def init_poolmanager(self, *args, **kwargs):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        ctx.set_ciphers("DEFAULT@SECLEVEL=1")
        if hasattr(ssl, "OP_LEGACY_SERVER_CONNECT"):
            ctx.options |= ssl.OP_LEGACY_SERVER_CONNECT
        kwargs["ssl_context"] = ctx
        super().init_poolmanager(*args, **kwargs)


def session() -> requests.Session:
    s = requests.Session()
    s.verify = False
    s.mount("https://", LegacySSLAdapter())
    s.headers["User-Agent"] = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
    )
    return s


def fetch(sess: requests.Session, url: str) -> str:
    r = sess.get(url, timeout=30)
    r.encoding = "utf-8"
    r.raise_for_status()
    return r.text


def analyze_434(html: str) -> None:
    (OUT / "main_434.html").write_text(html, encoding="utf-8")
    soup = BeautifulSoup(html, "lxml")

    print("=== /main/434 ===")
    entries: list[dict] = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        text = a.get_text(" ", strip=True)
        parent_text = ""
        if a.parent:
            parent_text = a.parent.get_text(" ", strip=True)[:120]
        is_pdf = ".pdf" in href.lower()
        is_none = href in ("#none", "#None", "javascript:void(0);", "javascript:;")
        if not (is_pdf or is_none or "보기" in text or "다운" in text):
            continue
        abs_url = urljoin(GUIDE, href) if is_pdf else href
        excluded = any(k in text or k in parent_text for k in EXCLUDE_KEYWORDS)
        entries.append(
            {
                "text": text,
                "href": href,
                "abs_url": abs_url,
                "parent": parent_text,
                "excluded": excluded,
            }
        )

    regex_pdfs = sorted(
        set(
            re.findall(r'(?:https?://www\.pknu\.ac\.kr)?(/upload/[^\s"\'<>]+\.pdf)', html, re.I)
            + re.findall(r'https?://www\.pknu\.ac\.kr/upload/[^\s"\'<>]+\.pdf', html, re.I)
        )
    )

    print(f"anchor entries: {len(entries)}")
    for i, e in enumerate(entries, 1):
        flag = " [EXCLUDE]" if e["excluded"] else ""
        print(f"  {i}. {e['text']!r} -> {e['href']!r}{flag}")
        if e["parent"] and e["parent"] != e["text"]:
            print(f"      parent: {e['parent'][:100]}")

    print(f"\nregex /upload/*.pdf in HTML: {len(regex_pdfs)}")
    for p in regex_pdfs:
        full = p if p.startswith("http") else urljoin(BASE, p)
        ex = any(k in p for k in EXCLUDE_KEYWORDS)
        print(f"  {'[EXCLUDE] ' if ex else ''}{full}")

    # onclick / data attributes
    print("\nonclick handlers with pdf/none:")
    for tag in soup.find_all(True):
        onclick = tag.get("onclick") or ""
        if onclick and ("pdf" in onclick.lower() or "open" in onclick.lower() or "view" in onclick.lower()):
            print(f"  <{tag.name}> onclick={onclick[:200]}")

    # script blocks mentioning pdf
    for script in soup.find_all("script"):
        txt = script.string or ""
        if ".pdf" in txt.lower() or "434" in txt:
            if len(txt) > 50:
                (OUT / "main_434_script_snippet.txt").write_text(txt[:8000], encoding="utf-8")
                print("\n(script with pdf saved to main_434_script_snippet.txt, len=%d)" % len(txt))
                break


def analyze_ebook(sess: requests.Session) -> None:
    print("\n=== E-하나로 eBook ===")
    html = fetch(sess, EBOOK)
    (OUT / "ebook_index.html").write_text(html, encoding="utf-8")
    print(f"index.html len={len(html)}")

    pdf_hints = re.findall(r'[^\s"\'<>]+\.pdf', html, re.I)
    print(f".pdf strings in index: {len(set(pdf_hints))}")
    for p in sorted(set(pdf_hints))[:20]:
        print(f"  {p}")

    # try config.js paths from spec
    base_ebook = f"{BASE}/ebook/col_life/kor/"
    candidates = [
        "files/mobile/javascript/config.js",
        "javascript/config.js",
        "mobile/javascript/config.js",
        "book_config.js",
        "config.js",
    ]
    for rel in candidates:
        url = urljoin(base_ebook, rel)
        try:
            r = sess.get(url, timeout=15)
            if r.status_code == 200 and len(r.content) > 50:
                path = OUT / rel.replace("/", "_")
                path.write_bytes(r.content)
                print(f"\nFOUND {url} ({len(r.content)} bytes) -> {path.name}")
                text = r.text[:5000]
                for m in re.finditer(r"[^\s\"']+\.pdf", text, re.I):
                    print(f"  pdf ref: {m.group()}")
        except requests.RequestException as exc:
            print(f"  miss {url}: {exc}")

    # grep entire ebook tree hints from html
    for pat in [
        r"bookPath\s*[=:]\s*['\"]([^'\"]+)",
        r"pdfPath\s*[=:]\s*['\"]([^'\"]+)",
        r"fliphtml5",
        r"/files/mobile/",
    ]:
        m = re.search(pat, html, re.I)
        if m:
            print(f"pattern {pat}: {m.group(0)[:120]}")


def test_pdf_extract(sess: requests.Session, pdf_url: str) -> None:
    print(f"\n=== PDF download + text test ===\nURL: {pdf_url}")
    r = sess.get(pdf_url, timeout=60)
    print(f"status={r.status_code} size={len(r.content)}")
    pdf_path = OUT / "sample.pdf"
    pdf_path.write_bytes(r.content)

    text_sample = ""
    for lib_name, import_fn in [("pdfplumber", "pdfplumber"), ("fitz", "fitz")]:
        try:
            if lib_name == "pdfplumber":
                import pdfplumber

                with pdfplumber.open(pdf_path) as pdf:
                    pages = min(2, len(pdf.pages))
                    parts = []
                    for i in range(pages):
                        t = pdf.pages[i].extract_text() or ""
                        parts.append(t)
                    text_sample = "\n".join(parts)
                print(f"pdfplumber OK: {len(text_sample)} chars (first 2 pages)")
            else:
                import fitz

                doc = fitz.open(pdf_path)
                parts = []
                for i in range(min(2, doc.page_count)):
                    parts.append(doc[i].get_text())
                text_sample = "\n".join(parts)
                print(f"PyMuPDF OK: {len(text_sample)} chars (first 2 pages)")
            break
        except ImportError:
            print(f"{lib_name} not installed")
        except Exception as exc:
            print(f"{lib_name} failed: {exc}")

    if text_sample:
        print("--- sample text (first 500 chars) ---")
        print(text_sample[:500])
    else:
        print("No text extracted (install pdfplumber or pymupdf)")


def resolve_media_ids(sess: requests.Session, html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    rows: list[dict] = []
    for div in soup.select("motion.div.uploadPdf, div.uploadPdf"):
        mid = div.get("data-id")
        if not mid:
            continue
        li = div.find_previous("li")
        title = li.get_text(" ", strip=True) if li else f"id={mid}"
        excluded = any(k in title for k in EXCLUDE_KEYWORDS)
        row = {"title": title, "media_id": mid, "excluded": excluded, "kind": "api"}
        if not excluded:
            r = sess.post(f"{BASE}/common/getMdaId.do", data={"no": mid}, timeout=20)
            try:
                path = r.json().get("response", "")
            except Exception:
                path = ""
            row["pdf_url"] = urljoin(BASE, "/upload/" + path.lstrip("/")) if path else ""
        rows.append(row)

    for a in soup.find_all("a", href=True):
        if ".pdf" not in a["href"].lower():
            continue
        title = a.get_text(" ", strip=True) or a.get("download", "PDF")
        rows.append(
            {
                "title": title,
                "media_id": None,
                "excluded": False,
                "kind": "direct",
                "pdf_url": urljoin(GUIDE, a["href"]),
            }
        )
    return rows


def analyze_ebook_pdf(sess: requests.Session) -> None:
    cfg = fetch(sess, f"{BASE}/ebook/col_life/kor/mobile/javascript/config.js")
    (OUT / "config_full.txt").write_text(cfg, encoding="utf-8")
    pdfs = sorted(set(re.findall(r'["\']([^"\']*\.pdf)["\']', cfg, re.I)))
    print("\n=== eBook config.js .pdf refs ===")
    for p in pdfs:
        print(f"  {p}")
    m = re.search(r"downloadURL\s*=\s*['\"]([^'\"]+)['\"]", cfg)
    if m:
        print(f"downloadURL = {m.group(1)}")
    m2 = re.search(r"totalPageCount\s*:\s*(\d+)", cfg)
    if m2:
        print(f"totalPageCount = {m2.group(1)}")


def main() -> None:
    sess = session()
    html434 = fetch(sess, GUIDE)
    analyze_434(html434)

    rows = resolve_media_ids(sess, html434)
    print("\n=== Resolved PDF targets (non-excluded) ===")
    crawl = []
    for row in rows:
        if row["excluded"]:
            print(f"[SKIP] {row['title']} (id={row.get('media_id')})")
            continue
        print(f"- {row['title']}")
        print(f"    {row.get('pdf_url','')}")
        if row.get("pdf_url"):
            crawl.append(row)

    print(f"\nCrawl count (excl contest): {len(crawl)}")

    analyze_ebook(sess)
    analyze_ebook_pdf(sess)

    test_url = crawl[0]["pdf_url"] if crawl else None
    if test_url:
        test_pdf_extract(sess, test_url)


if __name__ == "__main__":
    main()

"""Preprocess crawled PKNU rule artifacts for RAG.

The rule crawler stores three useful artifact types:
    1. JSON documents with crawler-normalized text fields.
    2. Raw HTML snapshots.
    3. Downloaded attachments.

This script can preprocess all three.  Duplicate text is allowed by design; each
output keeps a source_kind value so retrieval/debugging can distinguish where a
chunk came from.

Default inputs:
    files/rule/output/json
    files/rule/output/html
    files/rule/output/files

Default output:
    files/rule/preprocessed
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.text_cleaning import clean_extracted_text

DEFAULT_INPUT_ROOT = PROJECT_ROOT / "files" / "rule" / "output" / "json"
DEFAULT_HTML_ROOT = PROJECT_ROOT / "files" / "rule" / "output" / "html"
DEFAULT_FILES_ROOT = PROJECT_ROOT / "files" / "rule" / "output" / "files"
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "files" / "rule" / "preprocessed"
LOG_DIR = PROJECT_ROOT / "logs"
LOG_FILE = LOG_DIR / "rule_preprocessing.log"

DEFAULT_CHUNK_SIZE = 900
DEFAULT_CHUNK_OVERLAP = 120
SUPPORTED_ATTACHMENT_EXTS = {".pdf", ".docx", ".doc", ".hwp", ".hwpx", ".csv", ".txt"}
FORM_ATTACHMENT_PATTERN = re.compile(
    "(\ubcc4\uc9c0\\s*(?:\uc81c)?\\s*\\d+(?:\\s*\uc758\\s*\\d+)?\\s*\ud638?\\s*(?:\uc11c\uc2dd)?|"
    "\uc11c\uc2dd\\s*(?:\uc81c)?\\s*\\d+(?:\\s*\uc758\\s*\\d+)?)"
)
APPENDIX_TABLE_PATTERN = re.compile(
    "(\ubcc4\ud45c\\s*(?:\uc81c)?\\s*\\d+(?:\\s*\uc758\\s*\\d+)?\\s*\ud638?)"
)

log = logging.getLogger(__name__)


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


def normalize_text(text: object) -> str:
    if text is None:
        return ""
    return clean_extracted_text(text)


def normalize_inline(text: object) -> str:
    return re.sub(r"\s+", " ", normalize_text(text)).strip()


def rel_project_path(path: Path, root: Path = PROJECT_ROOT) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def stable_slug(*parts: object) -> str:
    raw = "|".join(normalize_inline(part) for part in parts)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:12]


def classify_attachment_metadata(*values: object) -> dict[str, Any]:
    text = normalize_inline(" ".join(str(value or "") for value in values))
    if not text:
        return {
            "document_kind": "rule_text",
            "attachment_kind": "",
            "is_form": False,
            "is_appendix_table": False,
        }

    appendix_match = APPENDIX_TABLE_PATTERN.search(text)
    if appendix_match:
        return {
            "document_kind": "appendix_table",
            "attachment_kind": "appendix_table",
            "is_form": False,
            "is_appendix_table": True,
            "appendix_number": appendix_match.group(1),
        }

    form_match = FORM_ATTACHMENT_PATTERN.search(text)
    if form_match:
        return {
            "document_kind": "form",
            "attachment_kind": "form",
            "is_form": True,
            "is_appendix_table": False,
            "form_number": form_match.group(1),
        }

    return {
        "document_kind": "attachment",
        "attachment_kind": "attachment",
        "is_form": False,
        "is_appendix_table": False,
    }


def output_path_for(input_file: Path, input_root: Path, output_root: Path) -> Path:
    rel = input_file.resolve().relative_to(input_root.resolve())
    return (output_root / rel).with_suffix(".json")


def load_rule_json_index(json_root: Path) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    docs_by_key: dict[str, dict[str, Any]] = {}
    attachments_by_path: dict[str, dict[str, Any]] = {}
    if not json_root.exists():
        return docs_by_key, attachments_by_path

    for json_path in json_root.rglob("*.json"):
        try:
            doc = json.loads(json_path.read_text(encoding="utf-8-sig"))
        except Exception:
            continue
        if not isinstance(doc, dict):
            continue

        category = normalize_inline(doc.get("category"))
        slug = normalize_inline(doc.get("slug")) or json_path.stem
        key = f"{category}/{slug}" if category else slug
        doc_info = {
            **build_provenance(doc, json_path),
            "source_json_path": rel_project_path(json_path),
        }
        docs_by_key[key] = doc_info

        for attachment in doc.get("attachments", []) or []:
            if not isinstance(attachment, dict):
                continue
            saved_path = normalize_inline(attachment.get("saved_path"))
            if not saved_path:
                continue
            abs_path = (PROJECT_ROOT / saved_path).resolve()
            attachments_by_path[abs_path.as_posix().lower()] = {
                **doc_info,
                "attachment_name": attachment.get("name", ""),
                "attachment_url": attachment.get("url", ""),
                "downloaded_from_url": attachment.get("downloaded_from_url", ""),
                "content_type": attachment.get("content_type", ""),
            }

    return docs_by_key, attachments_by_path


def html_path_for(doc: dict[str, Any], html_root: Path) -> Path | None:
    category = normalize_inline(doc.get("category"))
    slug = normalize_inline(doc.get("slug"))
    if not category or not slug:
        return None
    path = html_root / category / f"{slug}.html"
    return path if path.exists() else None


def extract_rule_html_text(path: Path, category: str) -> str:
    html = path.read_text(encoding="utf-8", errors="ignore")
    soup = BeautifulSoup(html, "html.parser")

    for tag in soup(["script", "style", "noscript", "svg", "canvas", "form", "iframe", "frame", "embed", "object"]):
        tag.decompose()
    for tag in soup.select("header, footer, nav, aside, .fileBox, .btn, .paging, .breadcrumb"):
        tag.decompose()

    candidates = []
    if category == "pknu_rule_bylaw":
        selectors = [".subTableDtl > section", ".subTableDtl", "article", "body"]
    else:
        selectors = ["#conScroll", "#contentBody", ".lawcon", "article", "body"]

    for selector in selectors:
        selected = soup.select_one(selector)
        if selected:
            candidates.append(selected)

    root = max(candidates or [soup], key=lambda tag: len(normalize_inline(tag.get_text(" ", strip=True))))
    lines = [normalize_inline(line) for line in root.get_text("\n", strip=True).splitlines()]
    lines = [line for line in lines if line]
    return "\n".join(lines)


def append_block(
    blocks: list[dict[str, Any]],
    seen: set[str],
    block_type: str,
    section: str,
    text: object,
    **extra: Any,
) -> None:
    value = normalize_text(text)
    if not value:
        return
    key = re.sub(r"\s+", " ", value)
    if key in seen:
        return
    seen.add(key)
    blocks.append(
        {
            "type": block_type,
            "section": section,
            "text": value,
            **extra,
        }
    )


def iter_text_items(items: object) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict) and normalize_text(item.get("text"))]


def extract_rule_json_blocks(doc: dict[str, Any], html_root: Path) -> tuple[list[dict[str, Any]], bool]:
    blocks: list[dict[str, Any]] = []
    seen: set[str] = set()

    append_block(blocks, seen, "title", "title", doc.get("title"))

    metadata_lines = []
    for label, key in (
        ("category", "category"),
        ("subcategory", "subcategory"),
        ("type", "type"),
        ("date", "date"),
        ("source_id", "source_id"),
        ("url", "url"),
    ):
        value = normalize_inline(doc.get(key))
        if value:
            metadata_lines.append(f"{label}: {value}")
    append_block(blocks, seen, "metadata", "metadata", "\n".join(metadata_lines))

    html_text = doc.get("html_text") or doc.get("page_content")
    append_block(blocks, seen, "body", "html_text", html_text)
    append_block(blocks, seen, "body", "page_content", doc.get("page_content"))
    append_block(blocks, seen, "body", "preview_content", doc.get("preview_content"))

    for index, preview in enumerate(iter_text_items(doc.get("preview_texts")), start=1):
        append_block(
            blocks,
            seen,
            "body",
            "preview_texts",
            preview.get("text"),
            source_url=preview.get("url", ""),
            source_index=index,
        )

    file_previews = iter_text_items(doc.get("file_preview_texts"))
    if not file_previews:
        file_previews = iter_text_items(doc.get("attachment_texts"))
    for index, preview in enumerate(file_previews, start=1):
        append_block(
            blocks,
            seen,
            "attachment_text",
            "file_preview_texts",
            preview.get("text"),
            attachment_name=preview.get("name", ""),
            attachment_url=preview.get("url", ""),
            saved_path=preview.get("saved_path", ""),
            source_index=index,
        )

    used_html_fallback = False
    body_chars = sum(len(block["text"]) for block in blocks if block["section"] != "metadata")
    if body_chars < 80:
        html_path = html_path_for(doc, html_root)
        if html_path:
            html_text = extract_rule_html_text(html_path, normalize_inline(doc.get("category")))
            append_block(
                blocks,
                seen,
                "body",
                "html_fallback",
                html_text,
                html_path=rel_project_path(html_path),
            )
            used_html_fallback = bool(html_text)

    return blocks, used_html_fallback


def chunk_blocks(
    blocks: list[dict[str, Any]],
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    current: list[str] = []
    current_sections: set[str] = set()
    current_len = 0
    chunk_id = 1

    def flush() -> None:
        nonlocal current, current_sections, current_len, chunk_id
        if not current:
            return
        text = "\n".join(current).strip()
        if text:
            chunks.append(
                {
                    "chunk_id": chunk_id,
                    "text": text,
                    "num_lines": len(text.splitlines()),
                    "num_chars": len(text),
                    "sections": sorted(current_sections),
                }
            )
            chunk_id += 1
        if overlap > 0 and text:
            tail = text[-overlap:].strip()
            current = [tail] if tail else []
            current_sections = {"overlap"} if tail else set()
            current_len = len(tail)
        else:
            current = []
            current_sections = set()
            current_len = 0

    def add_unit(unit: str, section: str) -> None:
        nonlocal current_len
        add_len = len(unit) + (1 if current else 0)
        if current and current_len + add_len > chunk_size:
            flush()
            add_len = len(unit) + (1 if current else 0)
        current.append(unit)
        current_sections.add(section)
        current_len += add_len

    for block in blocks:
        text = normalize_text(block.get("text"))
        if not text:
            continue
        section = normalize_inline(block.get("section")) or "unknown"
        for line in text.splitlines() or [text]:
            line = normalize_inline(line)
            if not line:
                continue
            if len(line) <= chunk_size:
                add_unit(line, section)
                continue
            start = 0
            step = max(1, chunk_size - overlap)
            while start < len(line):
                add_unit(line[start : start + chunk_size].strip(), section)
                start += step

    if current:
        text = "\n".join(current).strip()
        if text:
            chunks.append(
                {
                    "chunk_id": chunk_id,
                    "text": text,
                    "num_lines": len(text.splitlines()),
                    "num_chars": len(text),
                    "sections": sorted(current_sections),
                }
            )
    return chunks


def build_provenance(doc: dict[str, Any], input_file: Path) -> dict[str, Any]:
    return {
        "doc_title": doc.get("title", ""),
        "doc_url": doc.get("url", ""),
        "category": doc.get("category", ""),
        "subcategory": doc.get("subcategory", ""),
        "doc_type": doc.get("type", ""),
        "date": doc.get("date", ""),
        "source_id": doc.get("source_id", ""),
        "source_site": doc.get("source_site", ""),
        "crawled_at": doc.get("crawled_at", ""),
        "source_page_url": doc.get("url", ""),
        "source_json_path": rel_project_path(input_file),
    }


def write_preprocessed_result(
    input_file: Path,
    input_root: Path,
    output_root: Path,
    source_kind: str,
    blocks: list[dict[str, Any]],
    chunks: list[dict[str, Any]],
    provenance: dict[str, Any],
    used_html_fallback: bool = False,
) -> str:
    rel_in_input = rel_project_path(input_file, input_root)
    source_slug = provenance.get("source_slug") or stable_slug(source_kind, rel_in_input)
    if source_kind == "file":
        attachment_metadata = classify_attachment_metadata(
            input_file.name,
            provenance.get("attachment_name"),
            provenance.get("source_attachment_path"),
            provenance.get("source_page_url"),
        )
    else:
        attachment_metadata = {
            "document_kind": "rule_text",
            "attachment_kind": "",
            "is_form": False,
            "is_appendix_table": False,
        }
    result = {
        "slug": stable_slug("rule", source_kind, source_slug),
        "source_file": input_file.name,
        "source_path": rel_project_path(input_file),
        "source_relative_to_input": rel_in_input,
        "source_ext": input_file.suffix.lower(),
        "source_kind": source_kind,
        "processed_at": datetime.now().isoformat(timespec="seconds"),
        "num_blocks": len(blocks),
        "total_chars": sum(len(block["text"]) for block in blocks),
        "num_chunks": len(chunks),
        "used_html_fallback": used_html_fallback,
        "provenance": {
            **provenance,
            **attachment_metadata,
            "source_kind": source_kind,
        },
        "blocks": blocks,
        "chunks": chunks,
    }

    out_path = output_path_for(input_file, input_root, output_root / source_kind)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path.as_posix()


def preprocess_json_file(
    input_file: Path,
    input_root: Path,
    output_root: Path,
    html_root: Path,
    chunk_size: int,
    chunk_overlap: int,
) -> tuple[bool, str]:
    doc = json.loads(input_file.read_text(encoding="utf-8-sig"))
    if not isinstance(doc, dict):
        return False, "JSON root is not an object"

    blocks, used_html_fallback = extract_rule_json_blocks(doc, html_root)
    chunks = chunk_blocks(blocks, chunk_size=chunk_size, overlap=chunk_overlap)
    if not chunks:
        return False, "no chunks"

    provenance = {
        **build_provenance(doc, input_file),
        "source_slug": normalize_inline(doc.get("slug")) or stable_slug(rel_project_path(input_file, input_root)),
    }
    out_path = write_preprocessed_result(
        input_file=input_file,
        input_root=input_root,
        output_root=output_root,
        source_kind="json",
        blocks=blocks,
        chunks=chunks,
        provenance=provenance,
        used_html_fallback=used_html_fallback,
    )
    return True, out_path


def preprocess_html_file(
    input_file: Path,
    input_root: Path,
    output_root: Path,
    docs_by_key: dict[str, dict[str, Any]],
    chunk_size: int,
    chunk_overlap: int,
) -> tuple[bool, str]:
    category = input_file.parent.name
    slug = input_file.stem
    text = extract_rule_html_text(input_file, category)
    if not text:
        return False, "no text"

    blocks: list[dict[str, Any]] = []
    seen: set[str] = set()
    provenance = docs_by_key.get(f"{category}/{slug}", {})
    append_block(blocks, seen, "body", "html_text", text, html_path=rel_project_path(input_file))
    chunks = chunk_blocks(blocks, chunk_size=chunk_size, overlap=chunk_overlap)
    if not chunks:
        return False, "no chunks"

    out_path = write_preprocessed_result(
        input_file=input_file,
        input_root=input_root,
        output_root=output_root,
        source_kind="html",
        blocks=blocks,
        chunks=chunks,
        provenance={
            **provenance,
            "source_slug": slug,
            "category": provenance.get("category", category),
            "source_html_path": rel_project_path(input_file),
        },
    )
    return True, out_path


def preprocess_attachment_file(
    input_file: Path,
    input_root: Path,
    output_root: Path,
    attachments_by_path: dict[str, dict[str, Any]],
    chunk_size: int,
    chunk_overlap: int,
) -> tuple[bool, str]:
    if input_file.stat().st_size == 0:
        return False, "empty attachment file"

    try:
        from scripts.ce.preprocessing import extract_blocks
    except Exception as exc:
        raise RuntimeError(f"scripts.ce.preprocessing.extract_blocks import failed: {exc}") from exc

    blocks = extract_blocks(input_file)
    for block in blocks:
        block["text"] = clean_extracted_text(block.get("text", ""))
    blocks = [block for block in blocks if normalize_text(block.get("text"))]
    chunks = chunk_blocks(blocks, chunk_size=chunk_size, overlap=chunk_overlap)
    if not chunks:
        return False, "no chunks"

    provenance = attachments_by_path.get(input_file.resolve().as_posix().lower(), {})
    out_path = write_preprocessed_result(
        input_file=input_file,
        input_root=input_root,
        output_root=output_root,
        source_kind="file",
        blocks=blocks,
        chunks=chunks,
        provenance={
            **provenance,
            "source_slug": stable_slug(rel_project_path(input_file, input_root)),
            "source_attachment_path": rel_project_path(input_file),
        },
    )
    return True, out_path


def iter_json_files(input_root: Path) -> list[Path]:
    if not input_root.exists():
        return []
    return sorted(path for path in input_root.rglob("*.json") if path.is_file())


def iter_html_files(input_root: Path) -> list[Path]:
    if not input_root.exists():
        return []
    return sorted(path for path in input_root.rglob("*.html") if path.is_file())


def iter_attachment_files(input_root: Path) -> list[Path]:
    if not input_root.exists():
        return []
    return sorted(
        path
        for path in input_root.rglob("*")
        if path.is_file() and path.suffix.lower() in SUPPORTED_ATTACHMENT_EXTS
    )


def iter_failed_files_from_log(log_path: Path, project_root: Path = PROJECT_ROOT) -> list[Path]:
    if not log_path.exists():
        return []

    statuses: dict[Path, str] = {}
    pattern = re.compile(r"\[(OK|SKIP|FAIL):file\]\s+(.+?)(?:\s+->|\s+\(|$)")
    for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = pattern.search(line)
        if not match:
            continue
        status, path_text = match.group(1), match.group(2).strip()
        path = Path(path_text)
        if not path.is_absolute():
            path = project_root / path
        path = path.resolve()
        if not path.exists() or path.suffix.lower() not in SUPPORTED_ATTACHMENT_EXTS:
            continue
        statuses[path] = status
    return sorted(path for path, status in statuses.items() if status == "FAIL")


def run_batch(
    json_root: Path,
    html_root: Path,
    files_root: Path,
    output_root: Path,
    source_scope: str,
    failed_from_log: Path | None,
    dry_run: bool,
    chunk_size: int,
    chunk_overlap: int,
) -> None:
    docs_by_key, attachments_by_path = load_rule_json_index(json_root)
    tasks: list[tuple[str, Path, Path]] = []
    if failed_from_log:
        tasks.extend(("file", path, files_root) for path in iter_failed_files_from_log(failed_from_log))
    else:
        if source_scope in {"json", "all"}:
            tasks.extend(("json", path, json_root) for path in iter_json_files(json_root))
        if source_scope in {"html", "all"}:
            tasks.extend(("html", path, html_root) for path in iter_html_files(html_root))
        if source_scope in {"files", "all"}:
            tasks.extend(("file", path, files_root) for path in iter_attachment_files(files_root))

    if not tasks:
        if failed_from_log:
            log.info("No failed files found in %s", failed_from_log)
        else:
            log.info("No rule artifacts found for source_scope=%s", source_scope)
        return

    counts: dict[str, int] = {}
    for source_kind, _, _ in tasks:
        counts[source_kind] = counts.get(source_kind, 0) + 1
    log.info("Found rule artifacts: %s", ", ".join(f"{key}={value}" for key, value in sorted(counts.items())))

    ok = skipped = failed = 0
    for source_kind, input_file, input_root in tasks:
        rel = rel_project_path(input_file)
        if dry_run:
            log.info("[DRY-RUN:%s] %s", source_kind, rel)
            continue
        try:
            if source_kind == "json":
                log.info("[START:%s] %s", source_kind, rel)
                saved, info = preprocess_json_file(
                    input_file=input_file,
                    input_root=input_root,
                    output_root=output_root,
                    html_root=html_root,
                    chunk_size=chunk_size,
                    chunk_overlap=chunk_overlap,
                )
            elif source_kind == "html":
                log.info("[START:%s] %s", source_kind, rel)
                saved, info = preprocess_html_file(
                    input_file=input_file,
                    input_root=input_root,
                    output_root=output_root,
                    docs_by_key=docs_by_key,
                    chunk_size=chunk_size,
                    chunk_overlap=chunk_overlap,
                )
            else:
                log.info("[START:%s] %s", source_kind, rel)
                saved, info = preprocess_attachment_file(
                    input_file=input_file,
                    input_root=input_root,
                    output_root=output_root,
                    attachments_by_path=attachments_by_path,
                    chunk_size=chunk_size,
                    chunk_overlap=chunk_overlap,
                )
            if saved:
                ok += 1
                log.info("[OK:%s] %s -> %s", source_kind, rel, info)
            else:
                skipped += 1
                log.info("[SKIP:%s] %s (%s)", source_kind, rel, info)
        except Exception as exc:
            failed += 1
            log.warning("[FAIL:%s] %s (%s)", source_kind, rel, exc)

    if dry_run:
        log.info("Dry run complete")
    else:
        log.info("Done: saved=%d, skipped=%d, failed=%d", ok, skipped, failed)


def main() -> None:
    parser = argparse.ArgumentParser(description="Preprocess crawled PKNU rule artifacts for RAG.")
    parser.add_argument("--json-root", type=Path, default=DEFAULT_INPUT_ROOT)
    parser.add_argument("--html-root", type=Path, default=DEFAULT_HTML_ROOT)
    parser.add_argument("--files-root", type=Path, default=DEFAULT_FILES_ROOT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--source-scope", choices=["json", "html", "files", "all"], default="all")
    parser.add_argument(
        "--failed-from-log",
        type=Path,
        default=None,
        help="Reprocess only [FAIL:file] paths parsed from a preprocessing log.",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE)
    parser.add_argument("--chunk-overlap", type=int, default=DEFAULT_CHUNK_OVERLAP)
    args = parser.parse_args()

    configure_logging()
    run_batch(
        json_root=args.json_root.resolve(),
        html_root=args.html_root.resolve(),
        files_root=args.files_root.resolve(),
        output_root=args.output_root.resolve(),
        source_scope=args.source_scope,
        failed_from_log=args.failed_from_log.resolve() if args.failed_from_log else None,
        dry_run=args.dry_run,
        chunk_size=args.chunk_size,
        chunk_overlap=args.chunk_overlap,
    )


if __name__ == "__main__":
    main()

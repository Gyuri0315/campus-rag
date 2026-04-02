from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import os
import re
import site
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import uuid
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

SUPPORTED_EXTS = {
    ".pdf",
    ".docx",
    ".doc",
    ".hwp",
    ".hwpx",
    ".csv",
    ".txt",
}
DEFAULT_CHUNK_SIZE = 900
DEFAULT_CHUNK_OVERLAP = 120


def normalize_text(text: str) -> str:
    if not text:
        return ""
    text = text.replace("\u00a0", " ")
    text = " ".join(text.split())
    return text.strip()


def rel_project_path(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def make_slug(rel_path: str) -> str:
    return hashlib.md5(rel_path.encode("utf-8")).hexdigest()[:12]


def ensure_output_path(input_file: Path, input_root: Path, output_root: Path) -> Path:
    rel = input_file.resolve().relative_to(input_root.resolve())
    ext_dir = input_file.suffix.lower().lstrip(".") or "unknown"
    return (output_root / ext_dir / rel).with_suffix(".json")


def load_attachment_index(output_json_root: Path, project_root: Path) -> dict[str, dict]:
    index: dict[str, dict] = {}
    if not output_json_root.exists():
        return index

    for jf in output_json_root.rglob("*.json"):
        try:
            doc = json.loads(jf.read_text(encoding="utf-8"))
        except Exception:
            continue

        base_doc_info = {
            "doc_title": doc.get("title", ""),
            "doc_url": doc.get("url", ""),
            "category": doc.get("category", ""),
            "subcategory": doc.get("subcategory", ""),
        }
        for a in doc.get("attachments", []) or []:
            saved_path = a.get("saved_path", "")
            if not saved_path:
                continue
            saved_rel = rel_project_path(project_root / saved_path, project_root)
            index[saved_rel] = {
                **base_doc_info,
                "attachment_name": a.get("name", ""),
                "attachment_url": a.get("url", ""),
                "source_page_url": a.get("source_page_url", ""),
                "source_site": a.get("source_site", ""),
                "downloaded_from_url": a.get("downloaded_from_url", ""),
                "content_type": a.get("content_type", ""),
            }
    return index


def extract_pdf_blocks(path: Path) -> list[dict]:
    try:
        import fitz  # type: ignore
    except Exception as exc:
        raise RuntimeError(f"PDF parser import failed (PyMuPDF/fitz required): {exc}") from exc

    def is_likely_page_number(line: str) -> bool:
        line = line.strip()
        return bool(re.fullmatch(r"-?\s*\d+\s*-?", line))

    def detect_repeated_headers_footers(
        page_lines_list: list[list[str]], min_repeat: int = 3
    ) -> set[str]:
        candidates: list[str] = []
        for lines in page_lines_list:
            if not lines:
                continue
            top = lines[:2]
            bottom = lines[-2:] if len(lines) >= 2 else lines
            for line in top + bottom:
                line = normalize_text(line)
                if line:
                    candidates.append(line)
        counter = Counter(candidates)
        return {line for line, count in counter.items() if count >= min_repeat}

    def should_merge(prev_line: str, curr_line: str) -> bool:
        prev_line = prev_line.strip()
        curr_line = curr_line.strip()
        if not prev_line or not curr_line:
            return False
        if prev_line.endswith((".", "!", "?", ":", ";")):
            return False
        if len(prev_line) < 20:
            return False
        if re.match(r"^[a-z0-9(\[\-]", curr_line):
            return True
        return True

    def merge_lines_into_paragraphs(lines: list[str]) -> list[str]:
        paragraphs: list[str] = []
        buffer: list[str] = []
        for line in lines:
            line = normalize_text(line)
            if not line:
                if buffer:
                    paragraphs.append(" ".join(buffer).strip())
                    buffer = []
                continue
            if is_likely_page_number(line):
                continue
            if not buffer:
                buffer.append(line)
                continue
            prev_line = buffer[-1]
            if should_merge(prev_line, line):
                buffer.append(line)
            else:
                paragraphs.append(" ".join(buffer).strip())
                buffer = [line]
        if buffer:
            paragraphs.append(" ".join(buffer).strip())
        return paragraphs

    def remove_consecutive_duplicate_paragraphs(paragraphs: list[str]) -> list[str]:
        cleaned: list[str] = []
        prev = None
        for para in paragraphs:
            para = normalize_text(para)
            if not para:
                continue
            if para != prev:
                cleaned.append(para)
            prev = para
        return cleaned

    doc = fitz.open(str(path))
    all_page_lines: list[list[str]] = []
    for page in doc:
        text = page.get_text("text")
        lines = [normalize_text(line) for line in text.splitlines()]
        lines = [line for line in lines if line]
        all_page_lines.append(lines)

    repeated_headers_footers = detect_repeated_headers_footers(all_page_lines)
    blocks: list[dict] = []
    for page_num, lines in enumerate(all_page_lines, start=1):
        cleaned_lines: list[str] = []
        for line in lines:
            line = normalize_text(line)
            if not line:
                continue
            if line in repeated_headers_footers:
                continue
            if is_likely_page_number(line):
                continue
            cleaned_lines.append(line)

        paragraphs = merge_lines_into_paragraphs(cleaned_lines)
        paragraphs = remove_consecutive_duplicate_paragraphs(paragraphs)
        for para in paragraphs:
            blocks.append(
                {
                    "type": "paragraph",
                    "style": "Normal",
                    "page": page_num,
                    "text": para,
                }
            )

    doc.close()
    return blocks


def extract_docx_like_blocks(path: Path) -> list[dict]:
    suffix = path.suffix.lower()
    work_path = path
    if suffix == ".doc":
        try:
            import win32com.client  # type: ignore
        except Exception as exc:
            raise RuntimeError(f".doc conversion requires pywin32: {exc}") from exc

        converted = path.with_suffix(".docx")
        word = win32com.client.Dispatch("Word.Application")
        word.Visible = False
        try:
            doc = word.Documents.Open(str(path.resolve()))
            doc.SaveAs(str(converted.resolve()), FileFormat=16)
            doc.Close()
        finally:
            word.Quit()
        work_path = converted

    try:
        from docx import Document
    except Exception as exc:
        raise RuntimeError(f"DOCX parser import failed: {exc}") from exc

    doc = Document(str(work_path))
    blocks: list[dict] = []
    for para in doc.paragraphs:
        text = normalize_text(para.text)
        if not text:
            continue
        style_name = para.style.name if para.style else "Normal"
        block_type = "heading" if "Heading" in style_name else "paragraph"
        blocks.append({"type": block_type, "style": style_name, "text": text})

    for table in doc.tables:
        rows = []
        for row in table.rows:
            cells = [normalize_text(cell.text) for cell in row.cells]
            cells = [c for c in cells if c]
            if cells:
                rows.append(" | ".join(cells))
        if rows:
            blocks.append({"type": "table", "style": "Table", "text": "\n".join(rows)})

    return blocks


def extract_csv_blocks(path: Path) -> list[dict]:
    blocks: list[dict] = []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        sample = f.read(4096)
        f.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample)
        except csv.Error:
            dialect = csv.excel
        reader = csv.reader(f, dialect)
        for row_idx, row in enumerate(reader, start=1):
            cells = [normalize_text(c) for c in row if normalize_text(c)]
            if not cells:
                continue
            blocks.append(
                {
                    "type": "table_row",
                    "style": "CSV",
                    "row": row_idx,
                    "text": " | ".join(cells),
                }
            )
    return blocks


def extract_txt_blocks(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    lines = [normalize_text(line) for line in text.splitlines()]
    lines = [line for line in lines if line]
    blocks = []
    for idx, line in enumerate(lines, start=1):
        blocks.append(
            {
                "type": "paragraph",
                "style": "Text",
                "line": idx,
                "text": line,
            }
        )
    return blocks


def extract_hwpx_blocks(path: Path) -> list[dict]:
    blocks: list[dict] = []
    with zipfile.ZipFile(path) as zf:
        xml_names = [n for n in zf.namelist() if n.lower().endswith(".xml")]
        xml_names.sort()
        for name in xml_names:
            data = zf.read(name)
            try:
                root = ET.fromstring(data)
            except ET.ParseError:
                continue
            texts = []
            for elem in root.iter():
                if elem.text and normalize_text(elem.text):
                    texts.append(normalize_text(elem.text))
            if not texts:
                continue
            blocks.append(
                {
                    "type": "xml_text",
                    "style": "HWPX",
                    "section": name,
                    "text": " ".join(texts),
                }
            )
    return blocks


def extract_hwp_blocks(path: Path) -> list[dict]:
    def resolve_hwp_command(command_name: str) -> str | None:
        resolved = shutil.which(command_name)
        if resolved:
            return resolved

        if os.name != "nt":
            return None

        exe_name = f"{command_name}.exe"
        candidate_paths = [
            Path(sys.executable).resolve().parent / "Scripts" / exe_name,
            Path(sys.executable).resolve().parent / exe_name,
            Path(sysconfig.get_path("scripts")) / exe_name,
            Path(site.getuserbase()) / "Python313" / "Scripts" / exe_name,
        ]
        for candidate in candidate_paths:
            if candidate.exists():
                return str(candidate)
        return None

    def extract_hwp_blocks_from_html(path: Path) -> list[dict]:
        hwp5html = resolve_hwp_command("hwp5html")
        if not hwp5html:
            return []

        tmp_base = Path(tempfile.gettempdir()) / "campus_rag_hwp5html"
        tmp_base.mkdir(parents=True, exist_ok=True)
        token = uuid.uuid4().hex
        copied_hwp = tmp_base / f"{token}.hwp"
        html_file = tmp_base / f"{token}.xhtml"
        try:
            shutil.copy2(path, copied_hwp)
            proc = subprocess.run(
                [hwp5html, "--html", "--output", str(html_file), str(copied_hwp)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
                check=False,
            )
            if proc.returncode != 0:
                return []

            if not html_file.exists():
                return []

            try:
                root = ET.fromstring(html_file.read_text(encoding="utf-8", errors="ignore"))
            except ET.ParseError:
                return []

            def tag_name(elem: ET.Element) -> str:
                return elem.tag.rsplit("}", 1)[-1] if "}" in elem.tag else elem.tag

            def collect_text(elem: ET.Element) -> str:
                return normalize_text("".join(elem.itertext()))

            def table_to_text(table_elem: ET.Element) -> str:
                rows: list[str] = []
                for tr in table_elem.iter():
                    if tag_name(tr) != "tr":
                        continue
                    cells: list[str] = []
                    for child in list(tr):
                        if tag_name(child) not in {"td", "th"}:
                            continue
                        cell_text = collect_text(child)
                        if cell_text:
                            cells.append(cell_text)
                    if cells:
                        rows.append(" | ".join(cells))
                return "\n".join(rows)

            body = None
            for elem in root.iter():
                if tag_name(elem) == "body":
                    body = elem
                    break
            if body is None:
                return []

            blocks: list[dict] = []

            def collect_text_without_tables(elem: ET.Element) -> str:
                parts: list[str] = []
                if elem.text:
                    parts.append(elem.text)
                for child in list(elem):
                    if tag_name(child) != "table":
                        parts.append(collect_text_without_tables(child))
                    if child.tail:
                        parts.append(child.tail)
                return "".join(parts)

            def walk(elem: ET.Element) -> None:
                name = tag_name(elem)
                if name == "table":
                    table_text = table_to_text(elem)
                    if table_text:
                        blocks.append(
                            {
                                "type": "table",
                                "style": "HWP",
                                "text": table_text,
                            }
                        )
                    return
                if name == "p":
                    text = normalize_text(collect_text_without_tables(elem))
                    if text:
                        blocks.append(
                            {
                                "type": "paragraph",
                                "style": "HWP",
                                "text": text,
                            }
                        )
                for child in list(elem):
                    walk(child)

            walk(body)
            return blocks
        finally:
            try:
                copied_hwp.unlink(missing_ok=True)
            except Exception:
                pass
            try:
                html_file.unlink(missing_ok=True)
            except Exception:
                pass

    hwp5txt = resolve_hwp_command("hwp5txt")

    if not hwp5txt:
        raise RuntimeError(
            "hwp5txt command not found in PATH/current Python environment. "
            "Install pyhwp in the same interpreter and add its Scripts directory to PATH."
        )

    html_blocks = extract_hwp_blocks_from_html(path)
    if html_blocks:
        return html_blocks

    proc = subprocess.run(
        [hwp5txt, str(path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="ignore",
        check=False,
    )
    if proc.returncode != 0:
        err = proc.stderr.strip() or "unknown error"
        raise RuntimeError(f"hwp5txt failed: {err}")

    lines = [normalize_text(line) for line in proc.stdout.splitlines()]
    lines = [line for line in lines if line]

    blocks = []
    for idx, line in enumerate(lines, start=1):
        blocks.append(
            {
                "type": "paragraph",
                "style": "HWP",
                "line": idx,
                "text": line,
            }
        )
    return blocks


def extract_blocks(path: Path) -> list[dict]:
    ext = path.suffix.lower()
    if ext == ".pdf":
        return extract_pdf_blocks(path)
    if ext in {".docx", ".doc"}:
        return extract_docx_like_blocks(path)
    if ext == ".csv":
        return extract_csv_blocks(path)
    if ext == ".txt":
        return extract_txt_blocks(path)
    if ext == ".hwpx":
        return extract_hwpx_blocks(path)
    if ext == ".hwp":
        return extract_hwp_blocks(path)
    raise ValueError(f"unsupported extension: {ext}")


def chunk_blocks(
    blocks: list[dict],
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> list[dict]:
    texts = [normalize_text(b.get("text", "")) for b in blocks]
    texts = [t for t in texts if t]
    if not texts:
        return []

    chunks: list[dict] = []
    current: list[str] = []
    current_len = 0
    idx = 1

    for text in texts:
        add_len = len(text) + (1 if current else 0)
        if current and (current_len + add_len > chunk_size):
            chunk_text = "\n".join(current)
            chunks.append(
                {
                    "chunk_id": idx,
                    "text": chunk_text,
                    "num_lines": len(current),
                    "num_chars": len(chunk_text),
                }
            )
            idx += 1

            if overlap > 0:
                tail = chunk_text[-overlap:]
                current = [tail] if tail.strip() else []
                current_len = len(tail) if current else 0
            else:
                current = []
                current_len = 0

        current.append(text)
        current_len += len(text) + (1 if len(current) > 1 else 0)

    if current:
        chunk_text = "\n".join(current)
        chunks.append(
            {
                "chunk_id": idx,
                "text": chunk_text,
                "num_lines": len(current),
                "num_chars": len(chunk_text),
            }
        )
    return chunks


def save_preprocessed_file(
    input_file: Path,
    input_root: Path,
    output_root: Path,
    project_root: Path,
    attachment_index: dict[str, dict],
    chunk_size: int,
    chunk_overlap: int,
) -> tuple[bool, str]:
    rel = rel_project_path(input_file, project_root)
    ext = input_file.suffix.lower()
    if ext not in SUPPORTED_EXTS:
        return False, "unsupported"

    blocks = extract_blocks(input_file)
    blocks = [b for b in blocks if normalize_text(b.get("text", ""))]
    chunks = chunk_blocks(blocks, chunk_size=chunk_size, overlap=chunk_overlap)

    out_path = ensure_output_path(input_file, input_root, output_root)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    rel_in_input = rel_project_path(input_file, input_root)
    provenance = attachment_index.get(rel, {})
    result = {
        "slug": make_slug(rel),
        "source_file": input_file.name,
        "source_path": rel,
        "source_relative_to_input": rel_in_input,
        "source_ext": ext,
        "processed_at": datetime.now().isoformat(),
        "num_blocks": len(blocks),
        "total_chars": sum(len(b["text"]) for b in blocks),
        "num_chunks": len(chunks),
        "provenance": provenance,
        "blocks": blocks,
        "chunks": chunks,
    }

    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return True, out_path.as_posix()


def iter_target_files(input_root: Path) -> list[Path]:
    if not input_root.exists():
        return []
    files = []
    for p in input_root.rglob("*"):
        if p.is_file() and p.suffix.lower() in SUPPORTED_EXTS:
            files.append(p)
    return sorted(files)


def run_batch(
    input_root: Path,
    output_root: Path,
    output_json_root: Path,
    project_root: Path,
    dry_run: bool,
    chunk_size: int,
    chunk_overlap: int,
) -> None:
    attachment_index = load_attachment_index(output_json_root, project_root)
    files = iter_target_files(input_root)
    if not files:
        log.info("처리 대상 파일이 없습니다: %s", input_root)
        return

    log.info("처리 대상: %d개", len(files))
    ok = 0
    skipped = 0
    failed = 0

    for file_path in files:
        rel = rel_project_path(file_path, project_root)
        if dry_run:
            log.info("[DRY-RUN] %s", rel)
            continue
        try:
            saved, info = save_preprocessed_file(
                input_file=file_path,
                input_root=input_root,
                output_root=output_root,
                project_root=project_root,
                attachment_index=attachment_index,
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
            )
            if saved:
                ok += 1
                log.info("[OK] %s -> %s", rel, info)
            else:
                skipped += 1
                log.info("[SKIP] %s (%s)", rel, info)
        except Exception as exc:
            failed += 1
            log.warning("[FAIL] %s (%s)", rel, exc)

    if dry_run:
        log.info("DRY-RUN 완료")
    else:
        log.info("완료: 성공=%d, 건너뜀=%d, 실패=%d", ok, skipped, failed)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="output/files 첨부파일을 RAG용 전처리 JSON으로 변환"
    )
    parser.add_argument(
        "--input-root",
        type=Path,
        default=Path("output/files"),
        help="원본 첨부파일 루트 경로",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("preprocessed"),
        help="전처리 JSON 출력 루트 경로",
    )
    parser.add_argument(
        "--output-json-root",
        type=Path,
        default=Path("output/json"),
        help="크롤링 본문 JSON 루트 (첨부 provenance 조인용)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="실제 저장 없이 대상 파일만 확인",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=DEFAULT_CHUNK_SIZE,
        help="RAG 임베딩용 청크 최대 문자 수",
    )
    parser.add_argument(
        "--chunk-overlap",
        type=int,
        default=DEFAULT_CHUNK_OVERLAP,
        help="연속 청크 간 문자 오버랩 길이",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    project_root = Path.cwd()
    run_batch(
        input_root=args.input_root,
        output_root=args.output_root,
        output_json_root=args.output_json_root,
        project_root=project_root,
        dry_run=args.dry_run,
        chunk_size=args.chunk_size,
        chunk_overlap=args.chunk_overlap,
    )


if __name__ == "__main__":
    main()

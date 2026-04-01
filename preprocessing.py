from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import shutil
import subprocess
import xml.etree.ElementTree as ET
import zipfile
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
    return (output_root / rel).with_suffix(".json")


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
        from preprocessing_pdf import extract_pdf_paragraph_blocks
    except Exception as exc:
        raise RuntimeError(f"PDF extractor import failed: {exc}") from exc
    return extract_pdf_paragraph_blocks(str(path))


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
    hwp5txt = shutil.which("hwp5txt")
    if not hwp5txt:
        raise RuntimeError("hwp5txt command not found. Install pyhwp to parse .hwp")

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
        default=Path("output/preprocessed_files"),
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

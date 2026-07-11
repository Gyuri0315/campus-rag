from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import shutil
import sys
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path, PurePosixPath

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.text_cleaning import clean_extracted_text
from scripts.extractors.common import (
    DEFAULT_OCR_DPI,
    DEFAULT_OCR_LANGUAGE,
    DEFAULT_PDF_OCR_MODE,
    SUPPORTED_EXTS as EXTRACTABLE_EXTS,
    extract_blocks,
    normalize_text,
)

log = logging.getLogger(__name__)

LOG_DIR = PROJECT_ROOT / "logs"
LOG_FILE = LOG_DIR / "preprocessing.log"


def configure_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(LOG_FILE, encoding="utf-8"),
        ],
    )

DEFAULT_CHUNK_SIZE = 900
DEFAULT_CHUNK_OVERLAP = 120
ARCHIVE_EXTS = {".zip"}
SUPPORTED_EXTS = EXTRACTABLE_EXTS | ARCHIVE_EXTS
MAX_ARCHIVE_MEMBERS = 300
MAX_ARCHIVE_MEMBER_BYTES = 100 * 1024 * 1024
MAX_ARCHIVE_TOTAL_BYTES = 500 * 1024 * 1024


def parse_file_exts(values: list[str] | None) -> set[str] | None:
    if not values:
        return None

    exts: set[str] = set()
    for value in values:
        for item in value.split(","):
            ext = item.strip().lower()
            if not ext:
                continue
            if not ext.startswith("."):
                ext = f".{ext}"
            exts.add(ext)
    return exts or None


def rel_project_path(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def make_slug(rel_path: str) -> str:
    return hashlib.md5(rel_path.encode("utf-8")).hexdigest()[:12]


def ensure_output_path(
    input_file: Path,
    input_root: Path,
    output_root: Path,
    *,
    layout: str = "by_ext",
) -> Path:
    rel = input_file.resolve().relative_to(input_root.resolve())
    if layout == "flat":
        return (output_root / rel).with_suffix(".json")
    if layout == "by_ext":
        ext_dir = input_file.suffix.lower().lstrip(".") or "unknown"
        return (output_root / ext_dir / rel).with_suffix(".json")
    raise ValueError(f"unsupported layout: {layout}")


def is_safe_archive_member(name: str) -> bool:
    normalized = name.replace("\\", "/")
    path = PurePosixPath(normalized)
    if not normalized or normalized.endswith("/"):
        return False
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        return False
    if any(part == "__MACOSX" for part in path.parts):
        return False
    if path.name in {".DS_Store", "Thumbs.db"}:
        return False
    return True


def archive_member_output_path(
    archive_file: Path,
    input_root: Path,
    output_root: Path,
    member_name: str,
    *,
    layout: str = "by_ext",
) -> Path:
    archive_rel = archive_file.resolve().relative_to(input_root.resolve())
    member_rel = PurePosixPath(member_name.replace("\\", "/"))
    archive_stem_rel = archive_rel.with_suffix("")
    if layout == "flat":
        return (output_root / archive_stem_rel / Path(*member_rel.parts)).with_suffix(".json")
    if layout == "by_ext":
        return (output_root / "zip" / archive_stem_rel / Path(*member_rel.parts)).with_suffix(".json")
    raise ValueError(f"unsupported layout: {layout}")


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


def extract_crawled_json_provenance(path: Path) -> dict:
    try:
        doc = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}
    if not isinstance(doc, dict):
        return {}

    return {
        "doc_title": doc.get("title", ""),
        "doc_url": doc.get("url", ""),
        "category": doc.get("category", ""),
        "subcategory": doc.get("subcategory", ""),
        "doc_type": doc.get("type", "") or "post",
        "source_kind": "post",
        "date": doc.get("date", ""),
        "is_notice": doc.get("is_notice", False),
        "source_page_url": doc.get("url", ""),
        "source_site": doc.get("source_site", ""),
        "crawled_at": doc.get("crawled_at", ""),
    }


def build_metadata_fallback_blocks(
    input_file: Path,
    rel: str,
    rel_in_input: str,
    provenance: dict,
) -> list[dict]:
    fields = [
        ("문서 파일명", input_file.name),
        ("문서 경로", rel_in_input),
        ("문서 제목", provenance.get("doc_title", "")),
        ("게시글 URL", provenance.get("doc_url", "")),
        ("카테고리", provenance.get("category", "")),
        ("하위 카테고리", provenance.get("subcategory", "")),
        ("첨부파일명", provenance.get("attachment_name", "")),
        ("첨부파일 URL", provenance.get("attachment_url", "")),
        ("출처 페이지", provenance.get("source_page_url", "")),
        ("출처 사이트", provenance.get("source_site", "")),
        ("저장 경로", rel),
    ]
    lines = [f"{label}: {value}" for label, value in fields if normalize_text(str(value))]
    if not lines:
        return []
    return [{"type": "metadata_fallback", "style": "Metadata", "text": "\n".join(lines)}]


_OVERSIZED_BOUNDARY_RE = re.compile(r"[\n。．\.!?！？]")


def _split_oversized_text(text: str, max_size: int, overlap: int) -> list[str]:
    """단일 텍스트가 max_size를 넘으면 문장/공백 경계 기준 슬라이딩 분할.

    경계를 못 찾으면 문자 단위로 자른다. overlap은 인접 조각이 겹치도록 유지한다.
    """
    if len(text) <= max_size:
        return [text]
    if max_size <= overlap or max_size <= 0:
        step = max(max_size, 1)
        return [text[i : i + step] for i in range(0, len(text), step)]

    pieces: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + max_size, len(text))
        if end < len(text):
            # 윈도우 뒤쪽 20% 안에서 문장/문단 경계를 찾아 그 지점에서 자른다.
            search_from = start + int(max_size * 0.8)
            tail = text[search_from:end]
            matches = list(_OVERSIZED_BOUNDARY_RE.finditer(tail))
            if matches:
                end = search_from + matches[-1].end()
            else:
                ws_pos = text.rfind(" ", search_from, end)
                if ws_pos > search_from:
                    end = ws_pos + 1
        pieces.append(text[start:end])
        if end >= len(text):
            break
        start = max(start + 1, end - overlap)
    return pieces


def chunk_blocks(
    blocks: list[dict],
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> list[dict]:
    raw_texts = [normalize_text(b.get("text", "")) for b in blocks]
    raw_texts = [t for t in raw_texts if t]
    if not raw_texts:
        return []

    # paragraph 단위 청킹이라, 단일 block의 text가 chunk_size를 넘으면
    # 아래 누적 루프의 size 체크가 동작하지 않아 거대 chunk가 생긴다.
    # 미리 펼쳐서 모든 텍스트가 chunk_size 이하가 되도록 한다.
    texts: list[str] = []
    for t in raw_texts:
        if len(t) <= chunk_size:
            texts.append(t)
        else:
            texts.extend(_split_oversized_text(t, chunk_size, overlap))

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
    pdf_ocr_mode: str,
    ocr_language: str,
    ocr_dpi: int,
    layout: str = "by_ext",
) -> tuple[bool, str]:
    rel = rel_project_path(input_file, project_root)
    ext = input_file.suffix.lower()
    if ext in ARCHIVE_EXTS:
        return save_preprocessed_archive(
            archive_file=input_file,
            input_root=input_root,
            output_root=output_root,
            project_root=project_root,
            attachment_index=attachment_index,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            pdf_ocr_mode=pdf_ocr_mode,
            ocr_language=ocr_language,
            ocr_dpi=ocr_dpi,
            layout=layout,
        )
    if ext not in SUPPORTED_EXTS:
        return False, "unsupported"

    rel_in_input = rel_project_path(input_file, input_root)
    provenance = attachment_index.get(rel, {})
    if ext == ".json":
        provenance = {**extract_crawled_json_provenance(input_file), **provenance}
    elif not provenance.get("source_kind"):
        provenance = {**provenance, "source_kind": "attachment"}
    blocks = extract_blocks(
        input_file,
        pdf_ocr_mode=pdf_ocr_mode,
        ocr_language=ocr_language,
        ocr_dpi=ocr_dpi,
    )
    for block in blocks:
        block["text"] = clean_extracted_text(block.get("text", ""))
    blocks = [b for b in blocks if normalize_text(b.get("text", ""))]
    chunks = chunk_blocks(blocks, chunk_size=chunk_size, overlap=chunk_overlap)
    used_metadata_fallback = False
    if not chunks:
        fallback_blocks = build_metadata_fallback_blocks(input_file, rel, rel_in_input, provenance)
        fallback_chunks = chunk_blocks(fallback_blocks, chunk_size=chunk_size, overlap=0)
        if fallback_chunks:
            blocks = fallback_blocks
            chunks = fallback_chunks
            used_metadata_fallback = True

    out_path = ensure_output_path(input_file, input_root, output_root, layout=layout)
    out_path.parent.mkdir(parents=True, exist_ok=True)

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
        "used_metadata_fallback": used_metadata_fallback,
        "provenance": provenance,
        "blocks": blocks,
        "chunks": chunks,
    }

    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return True, out_path.as_posix()


def save_preprocessed_archive_member(
    archive_file: Path,
    member_file: Path,
    member_name: str,
    input_root: Path,
    output_root: Path,
    project_root: Path,
    archive_provenance: dict,
    chunk_size: int,
    chunk_overlap: int,
    pdf_ocr_mode: str,
    ocr_language: str,
    ocr_dpi: int,
    layout: str = "by_ext",
) -> tuple[bool, str]:
    archive_rel = rel_project_path(archive_file, project_root)
    archive_rel_in_input = rel_project_path(archive_file, input_root)
    member_ext = member_file.suffix.lower()
    if member_ext not in EXTRACTABLE_EXTS:
        return False, "unsupported archive member"

    member_source = f"{archive_rel}!/{member_name}"
    member_source_in_input = f"{archive_rel_in_input}!/{member_name}"
    provenance = {
        **archive_provenance,
        "source_kind": "archive_member",
        "archive_path": archive_rel,
        "archive_relative_to_input": archive_rel_in_input,
        "archive_member_path": member_name,
        "archive_file_name": archive_file.name,
    }

    blocks = extract_blocks(
        member_file,
        pdf_ocr_mode=pdf_ocr_mode,
        ocr_language=ocr_language,
        ocr_dpi=ocr_dpi,
    )
    for block in blocks:
        block["text"] = clean_extracted_text(block.get("text", ""))
    blocks = [b for b in blocks if normalize_text(b.get("text", ""))]
    chunks = chunk_blocks(blocks, chunk_size=chunk_size, overlap=chunk_overlap)
    used_metadata_fallback = False
    if not chunks:
        fallback_blocks = build_metadata_fallback_blocks(member_file, member_source, member_source_in_input, provenance)
        fallback_chunks = chunk_blocks(fallback_blocks, chunk_size=chunk_size, overlap=0)
        if fallback_chunks:
            blocks = fallback_blocks
            chunks = fallback_chunks
            used_metadata_fallback = True

    out_path = archive_member_output_path(archive_file, input_root, output_root, member_name, layout=layout)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    result = {
        "slug": make_slug(member_source),
        "source_file": PurePosixPath(member_name.replace("\\", "/")).name,
        "source_path": member_source,
        "source_relative_to_input": member_source_in_input,
        "source_ext": member_ext,
        "processed_at": datetime.now().isoformat(),
        "num_blocks": len(blocks),
        "total_chars": sum(len(b["text"]) for b in blocks),
        "num_chunks": len(chunks),
        "used_metadata_fallback": used_metadata_fallback,
        "provenance": provenance,
        "blocks": blocks,
        "chunks": chunks,
    }

    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return True, out_path.as_posix()


def save_preprocessed_archive(
    archive_file: Path,
    input_root: Path,
    output_root: Path,
    project_root: Path,
    attachment_index: dict[str, dict],
    chunk_size: int,
    chunk_overlap: int,
    pdf_ocr_mode: str,
    ocr_language: str,
    ocr_dpi: int,
    layout: str = "by_ext",
) -> tuple[bool, str]:
    archive_rel = rel_project_path(archive_file, project_root)
    archive_provenance = attachment_index.get(archive_rel, {})
    if not archive_provenance.get("source_kind"):
        archive_provenance = {**archive_provenance, "source_kind": "attachment_archive"}

    try:
        zf = zipfile.ZipFile(archive_file)
    except zipfile.BadZipFile:
        return False, "bad zip file"

    ok = 0
    skipped = 0
    failed = 0
    total_size = 0
    member_count = 0
    with zf, tempfile.TemporaryDirectory(prefix="campus_rag_zip_") as temp_dir:
        temp_root = Path(temp_dir)
        for info in zf.infolist():
            if info.is_dir():
                continue
            if not is_safe_archive_member(info.filename):
                skipped += 1
                log.info("[SKIP:zip] %s!/%s (unsafe archive member)", archive_rel, info.filename)
                continue
            member_ext = PurePosixPath(info.filename.replace("\\", "/")).suffix.lower()
            if member_ext not in EXTRACTABLE_EXTS:
                skipped += 1
                continue
            member_count += 1
            if member_count > MAX_ARCHIVE_MEMBERS:
                skipped += 1
                log.warning("[SKIP:zip] %s (archive member limit exceeded: %d)", archive_rel, MAX_ARCHIVE_MEMBERS)
                break
            if info.file_size > MAX_ARCHIVE_MEMBER_BYTES:
                skipped += 1
                log.warning("[SKIP:zip] %s!/%s (member too large: %d bytes)", archive_rel, info.filename, info.file_size)
                continue
            total_size += info.file_size
            if total_size > MAX_ARCHIVE_TOTAL_BYTES:
                skipped += 1
                log.warning("[SKIP:zip] %s (archive total size limit exceeded: %d bytes)", archive_rel, MAX_ARCHIVE_TOTAL_BYTES)
                break

            member_path = PurePosixPath(info.filename.replace("\\", "/"))
            temp_member = temp_root / Path(*member_path.parts)
            temp_member.parent.mkdir(parents=True, exist_ok=True)
            try:
                with zf.open(info) as src, temp_member.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
                saved, info_text = save_preprocessed_archive_member(
                    archive_file=archive_file,
                    member_file=temp_member,
                    member_name=info.filename.replace("\\", "/"),
                    input_root=input_root,
                    output_root=output_root,
                    project_root=project_root,
                    archive_provenance=archive_provenance,
                    chunk_size=chunk_size,
                    chunk_overlap=chunk_overlap,
                    pdf_ocr_mode=pdf_ocr_mode,
                    ocr_language=ocr_language,
                    ocr_dpi=ocr_dpi,
                    layout=layout,
                )
                if saved:
                    ok += 1
                    log.info("[OK:zip] %s!/%s -> %s", archive_rel, info.filename, info_text)
                else:
                    skipped += 1
                    log.info("[SKIP:zip] %s!/%s (%s)", archive_rel, info.filename, info_text)
            except RuntimeError as exc:
                failed += 1
                message = str(exc)
                if "password required" in message.lower() or "encrypted" in message.lower():
                    log.warning("[FAIL:zip] %s!/%s (encrypted member)", archive_rel, info.filename)
                else:
                    log.warning("[FAIL:zip] %s!/%s (%s)", archive_rel, info.filename, exc)
            except Exception as exc:
                failed += 1
                log.warning("[FAIL:zip] %s!/%s (%s)", archive_rel, info.filename, exc)

    if ok:
        return True, f"archive members processed: ok={ok}, skipped={skipped}, failed={failed}"
    return False, f"no supported archive members processed: skipped={skipped}, failed={failed}"


def iter_target_files(input_root: Path, file_exts: set[str] | None = None) -> list[Path]:
    if not input_root.exists():
        return []
    allowed_exts = file_exts or SUPPORTED_EXTS
    files = []
    for p in input_root.rglob("*"):
        if p.is_file() and p.suffix.lower() in allowed_exts:
            files.append(p)
    return sorted(files)


def iter_failed_files_from_log(
    log_path: Path,
    input_root: Path,
    project_root: Path,
    file_exts: set[str] | None = None,
) -> list[Path]:
    if not log_path.exists():
        return []

    statuses: dict[Path, str] = {}
    pattern = re.compile(r"\[(OK|SKIP|FAIL)\]\s+(.+?)(?:\s+->|\s+\(|$)")
    allowed_exts = file_exts or SUPPORTED_EXTS
    for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = pattern.search(line)
        if not match:
            continue
        status, path_text = match.group(1), match.group(2).strip()
        path = Path(path_text)
        if not path.is_absolute():
            path = project_root / path
        path = path.resolve()
        if not path.exists() or path.suffix.lower() not in allowed_exts:
            continue
        try:
            path.relative_to(input_root)
        except ValueError:
            continue
        statuses[path] = status

    return sorted(path for path, status in statuses.items() if status == "FAIL")


def run_batch(
    input_root: Path,
    output_root: Path,
    output_json_root: Path,
    project_root: Path,
    failed_from_log: Path | None,
    dry_run: bool,
    chunk_size: int,
    chunk_overlap: int,
    pdf_ocr_mode: str,
    ocr_language: str,
    ocr_dpi: int,
    layout: str = "by_ext",
    file_exts: set[str] | None = None,
) -> None:
    input_root = input_root.resolve()
    output_root = output_root.resolve()
    output_json_root = output_json_root.resolve()
    project_root = project_root.resolve()

    attachment_index = load_attachment_index(output_json_root, project_root)
    files = (
        iter_failed_files_from_log(failed_from_log, input_root, project_root, file_exts=file_exts)
        if failed_from_log
        else iter_target_files(input_root, file_exts=file_exts)
    )
    if not files:
        if failed_from_log:
            log.info("No failed files found in %s", failed_from_log)
        else:
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
                pdf_ocr_mode=pdf_ocr_mode,
                ocr_language=ocr_language,
                ocr_dpi=ocr_dpi,
                layout=layout,
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
        description="files/ce/output의 게시글 JSON과 첨부 원문파일을 RAG용 전처리 JSON으로 변환"
    )
    parser.add_argument(
        "--input-root",
        type=Path,
        default=PROJECT_ROOT / "files" / "ce" / "output" / "files",
        help="원본 크롤링 결과 루트 경로",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=PROJECT_ROOT / "files" / "ce" / "preprocessed",
        help="전처리 JSON 출력 루트 경로",
    )
    parser.add_argument(
        "--output-json-root",
        type=Path,
        default=PROJECT_ROOT / "files" / "ce" / "output" / "json",
        help="크롤링 본문 JSON 루트 (첨부 provenance 조인용)",
    )
    parser.add_argument(
        "--failed-from-log",
        type=Path,
        default=None,
        help="전처리 로그에서 마지막 상태가 [FAIL]인 파일만 재처리",
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
    parser.add_argument(
        "--pdf-ocr",
        choices=["auto", "never", "always"],
        default=DEFAULT_PDF_OCR_MODE,
        help="PDF OCR fallback 사용 방식",
    )
    parser.add_argument(
        "--ocr-language",
        default=DEFAULT_OCR_LANGUAGE,
        help="Tesseract OCR 언어 설정",
    )
    parser.add_argument(
        "--ocr-dpi",
        type=int,
        default=DEFAULT_OCR_DPI,
        help="PDF 페이지를 OCR 이미지로 렌더링할 DPI",
    )
    parser.add_argument(
        "--layout",
        choices=["by_ext", "flat"],
        default="by_ext",
        help=(
            "출력 경로 레이아웃. flat은 crawled JSON처럼 output/json 입력 시 "
            "preprocessed/json/<category>/... 구조를 만든다."
        ),
    )
    parser.add_argument(
        "--file-ext",
        "--file-exts",
        dest="file_exts",
        nargs="+",
        default=None,
        help="Limit preprocessing to these file extensions, e.g. --file-ext pdf hwp or --file-ext pdf,hwp.",
    )
    args = parser.parse_args()

    configure_logging()
    project_root = PROJECT_ROOT
    file_exts = parse_file_exts(args.file_exts)
    unsupported_exts = sorted((file_exts or set()) - SUPPORTED_EXTS)
    if unsupported_exts:
        parser.error(
            "unsupported --file-ext value(s): "
            + ", ".join(unsupported_exts)
            + ". Supported: "
            + ", ".join(sorted(SUPPORTED_EXTS))
        )
    run_batch(
        input_root=args.input_root,
        output_root=args.output_root,
        output_json_root=args.output_json_root,
        project_root=project_root,
        failed_from_log=args.failed_from_log.resolve() if args.failed_from_log else None,
        dry_run=args.dry_run,
        chunk_size=args.chunk_size,
        chunk_overlap=args.chunk_overlap,
        pdf_ocr_mode=args.pdf_ocr,
        ocr_language=args.ocr_language,
        ocr_dpi=args.ocr_dpi,
        layout=args.layout,
        file_exts=file_exts,
    )


if __name__ == "__main__":
    main()

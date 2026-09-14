"""Shared document schema helpers for crawler outputs.

The helpers in this module are intentionally independent of individual crawler
implementations so crawler modules can use them without circular imports.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

SCHEMA_VERSION = "1.0"
try:
    KST = ZoneInfo("Asia/Seoul")
except ZoneInfoNotFoundError:  # pragma: no cover - only for hosts without tzdata
    KST = timezone(timedelta(hours=9))
STANDARD_DOCUMENT_TYPES = {
    "notice",
    "static_page",
    "guide",
    "law",
    "regulation",
    "bylaw",
    "guideline",
}
REQUIRED_DOCUMENT_FIELDS = {
    "schema_version",
    "id",
    "slug",
    "source_site",
    "source_dataset",
    "source_id",
    "url",
    "title",
    "author",
    "category",
    "subcategory",
    "type",
    "tags",
    "published_at",
    "updated_at",
    "effective_at",
    "content",
    "content_hash",
    "content_source",
    "attachments",
    "crawl",
    "metadata",
}
ATTACHMENT_TEXT_STATUSES = {"success", "skipped", "failed", "not_attempted"}
REQUIRED_ATTACHMENT_FIELDS = {
    "id",
    "name",
    "url",
    "final_url",
    "saved_path",
    "downloaded",
    "content_type",
    "size_bytes",
    "sha256",
    "text",
    "error",
}
RUN_STAT_FIELDS = (
    "discovered", "requested", "new", "updated", "unchanged", "deleted",
    "skipped", "failed", "attachments_discovered", "attachments_downloaded",
    "attachments_failed",
)
RUN_STATUSES = {"success", "partial_success", "failed", "cancelled"}
RUN_MODES = {"full", "incremental", "recent", "smoke"}


@dataclass
class CrawlStats:
    discovered: int = 0
    requested: int = 0
    new: int = 0
    updated: int = 0
    unchanged: int = 0
    deleted: int = 0
    skipped: int = 0
    failed: int = 0
    attachments_discovered: int = 0
    attachments_downloaded: int = 0
    attachments_failed: int = 0

    def add(self, other: "CrawlStats") -> "CrawlStats":
        for name in RUN_STAT_FIELDS:
            setattr(self, name, int(getattr(self, name)) + int(getattr(other, name)))
        return self

    def count_attachments(self, attachments: object) -> None:
        if not isinstance(attachments, list):
            return
        self.attachments_discovered += len(attachments)
        self.attachments_downloaded += sum(
            1 for item in attachments if isinstance(item, dict) and item.get("downloaded") is True
        )
        self.attachments_failed += sum(
            1 for item in attachments if isinstance(item, dict) and item.get("error") is not None
        )

    def to_dict(self) -> dict[str, int]:
        return {name: int(getattr(self, name)) for name in RUN_STAT_FIELDS}


@dataclass
class RunError:
    code: str
    source_id: str | None = None
    url: str | None = None
    message: str = ""
    retryable: bool = False


@dataclass
class RunResult:
    dataset: str
    mode: str
    started_at: str = field(default_factory=lambda: datetime.now(KST).isoformat(timespec="seconds"))
    stats: CrawlStats = field(default_factory=CrawlStats)
    schema_version: str = SCHEMA_VERSION
    run_id: str = ""
    finished_at: str | None = None
    elapsed_seconds: float = 0.0
    status: str = "success"
    errors: list[RunError] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.mode not in RUN_MODES:
            raise ValueError(f"unsupported crawl mode: {self.mode}")
        if not self.run_id:
            stamp = datetime.fromisoformat(self.started_at).strftime("%Y%m%dT%H%M%S")
            self.run_id = f"{sanitize_run_component(self.dataset)}-{stamp}"

    def add_error(
        self, code: str, message: object, *, source_id: object = None,
        url: object = None, retryable: bool = False,
    ) -> None:
        self.errors.append(RunError(
            code=str(code), source_id=str(source_id) if source_id is not None else None,
            url=str(url) if url is not None else None, message=str(message), retryable=bool(retryable),
        ))

    def finish(self, status: str | None = None) -> "RunResult":
        finished = datetime.now(KST)
        started = datetime.fromisoformat(self.started_at)
        self.finished_at = finished.isoformat(timespec="seconds")
        self.elapsed_seconds = max(0.0, round((finished - started).total_seconds(), 3))
        if status is not None:
            if status not in RUN_STATUSES:
                raise ValueError(f"unsupported run status: {status}")
            self.status = status
        elif self.stats.failed or self.stats.attachments_failed or self.errors:
            self.status = "partial_success" if self.stats.requested or self.stats.new or self.stats.updated else "failed"
        else:
            self.status = "success"
        return self

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version, "run_id": self.run_id,
            "dataset": self.dataset, "mode": self.mode, "started_at": self.started_at,
            "finished_at": self.finished_at, "elapsed_seconds": float(self.elapsed_seconds),
            "status": self.status, "stats": self.stats.to_dict(),
            "errors": [asdict(error) for error in self.errors],
        }

    def save(self, project_root: Path) -> Path:
        if self.finished_at is None:
            self.finish()
        from scripts.crawlers.common.storage import get_dataset_paths

        path = get_dataset_paths(project_root, self.dataset).run_result(self.run_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    @property
    def exit_code(self) -> int:
        # Partial success remains operationally successful; failures are visible in the JSON result.
        return 1 if self.status == "failed" else 0


def sanitize_run_component(value: object) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "dataset")).strip("-._") or "dataset"


def classify_document(existing_hash: object, current_hash: object) -> str:
    if not existing_hash:
        return "new"
    return "unchanged" if str(existing_hash) == str(current_hash) else "updated"


def log_run_result(logger: Any, result: RunResult, path: Path | None = None) -> None:
    logger.info("RUN_RESULT %s", json.dumps(result.to_dict(), ensure_ascii=False, separators=(",", ":")))
    if path is not None:
        logger.info("RUN_RESULT_FILE %s", path)


def normalize_content(value: object) -> str:
    """Return the canonical text used for document content hashes."""
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def content_sha256(value: object) -> str:
    return hashlib.sha256(normalize_content(value).encode("utf-8")).hexdigest()


def sanitize_attachment_filename(filename: object) -> str:
    """Return a basename that is safe on Windows and POSIX filesystems."""
    name = Path(str(filename or "")).name
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    name = name or "attachment"
    reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
    if Path(name).stem.upper() in reserved:
        name = f"_{name}"
    return name


def unique_attachment_filename(filename: object, used_names: set[str]) -> str:
    """Reserve a collision-free filename using a deterministic numeric suffix."""
    safe_name = sanitize_attachment_filename(filename)
    used_folded = {item.casefold() for item in used_names}
    if safe_name.casefold() not in used_folded:
        used_names.add(safe_name)
        return safe_name
    stem = Path(safe_name).stem
    suffix = Path(safe_name).suffix
    for index in range(2, 10_000):
        candidate = f"{stem}_{index}{suffix}"
        if candidate.casefold() not in used_folded:
            used_names.add(candidate)
            return candidate
    raise RuntimeError(f"could not find a unique filename for {safe_name}")


def project_relative_path(path: object, project_root: Path) -> str | None:
    if path is None or str(path).strip() == "":
        return None
    candidate = Path(str(path))
    absolute = candidate if candidate.is_absolute() else project_root / candidate
    try:
        return absolute.resolve().relative_to(project_root.resolve()).as_posix()
    except ValueError as exc:
        raise ValueError(f"attachment path is outside project root: {path}") from exc


def attachment_error(code: str, message: object, retryable: bool) -> dict[str, Any]:
    return {
        "code": str(code),
        "message": str(message),
        "retryable": bool(retryable),
    }


def attachment_text(
    status: str = "not_attempted",
    *,
    content: str | None = None,
    characters: int | None = None,
    extractor: str | None = None,
    error: object = None,
) -> dict[str, Any]:
    if status not in ATTACHMENT_TEXT_STATUSES:
        raise ValueError(f"unsupported attachment text status: {status}")
    text_content = str(content) if content is not None else None
    return {
        "status": status,
        "content": text_content,
        "characters": int(characters if characters is not None else len(text_content or "")),
        "extractor": str(extractor) if extractor else None,
        "error": str(error) if error else None,
    }


def _file_metadata(saved_path: str | None, project_root: Path) -> tuple[int | None, str | None]:
    if not saved_path:
        return None, None
    path = project_root / saved_path
    if not path.is_file():
        return None, None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return path.stat().st_size, digest.hexdigest()


def build_attachment(
    *,
    index: int,
    name: object,
    url: object,
    project_root: Path,
    final_url: object = None,
    saved_path: object = None,
    downloaded: bool = False,
    content_type: object = None,
    text: dict[str, Any] | None = None,
    error: dict[str, Any] | None = None,
    legacy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the canonical attachment record and retain requested legacy keys."""
    relative_path = project_relative_path(saved_path, project_root)
    actually_downloaded = bool(downloaded and relative_path)
    try:
        size_bytes, file_sha256 = (
            _file_metadata(relative_path, project_root) if actually_downloaded else (None, None)
        )
    except OSError as exc:
        size_bytes, file_sha256 = None, None
        actually_downloaded = False
        error = error or attachment_error("FILE_METADATA_FAILED", exc, True)
    record = dict(legacy or {})
    record.update(
        {
            "id": f"attachment-{index:03d}",
            "name": sanitize_attachment_filename(name),
            "url": str(url or ""),
            "final_url": str(final_url) if final_url else None,
            "saved_path": relative_path,
            "downloaded": actually_downloaded,
            "content_type": str(content_type).split(";", 1)[0].strip().lower() if content_type else None,
            "size_bytes": size_bytes,
            "sha256": file_sha256,
            "text": text or attachment_text(),
            "error": error,
        }
    )
    # Legacy readers use this alias. Keep it synchronized during migration.
    record["downloaded_from_url"] = record["final_url"]
    return record


def normalize_attachment(
    attachment: dict[str, Any],
    *,
    index: int,
    project_root: Path,
) -> dict[str, Any]:
    """Upgrade a previously saved or partially populated attachment record."""
    saved_path = attachment.get("saved_path")
    relative_path = project_relative_path(saved_path, project_root) if saved_path else None
    downloaded = bool(attachment.get("downloaded", bool(relative_path)))
    return build_attachment(
        index=index,
        name=attachment.get("name") or f"attachment-{index}",
        url=attachment.get("url"),
        project_root=project_root,
        final_url=attachment.get("final_url") or attachment.get("downloaded_from_url"),
        saved_path=relative_path,
        downloaded=downloaded,
        content_type=attachment.get("content_type"),
        text=attachment.get("text") if isinstance(attachment.get("text"), dict) else None,
        error=attachment.get("error") if isinstance(attachment.get("error"), dict) else None,
        legacy=attachment,
    )


def validate_attachment(attachment: dict[str, Any], project_root: Path) -> list[str]:
    errors: list[str] = []
    missing = sorted(REQUIRED_ATTACHMENT_FIELDS - attachment.keys())
    if missing:
        return [f"missing attachment fields: {', '.join(missing)}"]
    if not re.fullmatch(r"attachment-\d{3}", str(attachment.get("id") or "")):
        errors.append("attachment id must use attachment-NNN")
    if not isinstance(attachment.get("downloaded"), bool):
        errors.append("downloaded must be boolean")
    if attachment.get("size_bytes") is not None and not isinstance(attachment.get("size_bytes"), int):
        errors.append("size_bytes must be an integer or null")
    text_info = attachment.get("text")
    if not isinstance(text_info, dict):
        errors.append("text must be an object")
    else:
        if text_info.get("status") not in ATTACHMENT_TEXT_STATUSES:
            errors.append("text.status is invalid")
        if not isinstance(text_info.get("characters"), int):
            errors.append("text.characters must be an integer")
    saved_path = attachment.get("saved_path")
    if saved_path:
        if Path(str(saved_path)).is_absolute():
            errors.append("saved_path must be project-relative")
        else:
            try:
                project_relative_path(saved_path, project_root)
            except ValueError as exc:
                errors.append(str(exc))
    if attachment.get("downloaded") and (attachment.get("size_bytes") is None or not attachment.get("sha256")):
        errors.append("downloaded attachments require size_bytes and sha256")
    error_info = attachment.get("error")
    if error_info is not None:
        if not isinstance(error_info, dict) or not {"code", "message", "retryable"}.issubset(error_info):
            errors.append("error must contain code, message and retryable")
    return errors


def canonical_source_url(url: object) -> str:
    """Normalize a URL without discarding query parameters that may be IDs."""
    value = str(url or "").strip()
    if not value:
        return ""
    parts = urlsplit(value)
    return urlunsplit(
        (
            parts.scheme.lower(),
            parts.netloc.lower(),
            parts.path or "/",
            parts.query,
            "",
        )
    )


def url_source_id(url: object) -> str:
    canonical = canonical_source_url(url)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:20]


def document_id(source_dataset: str, source_id: object) -> str:
    dataset = str(source_dataset).strip()
    raw_source_id = str(source_id).strip()
    if not dataset or not raw_source_id:
        raise ValueError("source_dataset and source_id are required")
    return f"{dataset}:{raw_source_id}"


def document_slug(source_dataset: str, source_id: object) -> str:
    """Create a filename-safe, title-independent slug from the logical ID."""
    logical_id = document_id(source_dataset, source_id)
    return hashlib.sha256(logical_id.encode("utf-8")).hexdigest()[:16]


def now_kst() -> str:
    return datetime.now(KST).isoformat(timespec="seconds")


def _coerce_datetime(value: object) -> tuple[str | None, str | None, bool]:
    """Return (ISO value, precision, inferred) in KST."""
    if value is None or str(value).strip() == "":
        return None, None, False

    if isinstance(value, datetime):
        parsed = value
        precision = "datetime"
        inferred = value.tzinfo is None
    elif isinstance(value, date):
        parsed = datetime.combine(value, time.min)
        precision = "date"
        inferred = True
    else:
        raw = str(value).strip()
        if re.fullmatch(r"\d{4}", raw):
            parsed = datetime(int(raw), 1, 1)
            precision = "year"
            inferred = True
        elif re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
            parsed = datetime.fromisoformat(raw)
            precision = "date"
            inferred = True
        else:
            try:
                parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError:
                return None, None, False
            precision = "datetime"
            inferred = parsed.tzinfo is None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=KST)
    else:
        parsed = parsed.astimezone(KST)
    return parsed.isoformat(timespec="seconds"), precision, inferred


def datetime_kst(value: object) -> str | None:
    return _coerce_datetime(value)[0]


def apply_common_schema(
    doc: dict[str, Any],
    *,
    source_dataset: str,
    source_id: object,
    source_site: str,
    document_type: str,
    content_source: str,
    published_at: object = None,
    updated_at: object = None,
    effective_at: object = None,
    author: object = None,
    metadata: dict[str, Any] | None = None,
    crawled_at: object = None,
) -> dict[str, Any]:
    """Add schema 1.0 fields while retaining all legacy crawler fields."""
    if document_type not in STANDARD_DOCUMENT_TYPES:
        raise ValueError(f"unsupported document type: {document_type}")
    if not re.match(r"^https?://", source_site):
        raise ValueError("source_site must be an absolute HTTP(S) URL")

    source_id_text = str(source_id).strip()
    published_iso, published_precision, published_inferred = _coerce_datetime(published_at)
    updated_iso, _, _ = _coerce_datetime(updated_at)
    effective_iso, _, _ = _coerce_datetime(effective_at)
    crawl_iso = datetime_kst(crawled_at) or now_kst()
    merged_metadata = dict(metadata or {})
    if published_precision:
        merged_metadata.setdefault("published_at_precision", published_precision)
        merged_metadata.setdefault("published_at_inferred", published_inferred)

    result = dict(doc)
    result.update(
        {
            "schema_version": SCHEMA_VERSION,
            "id": document_id(source_dataset, source_id_text),
            "slug": document_slug(source_dataset, source_id_text),
            "source_site": source_site,
            "source_dataset": source_dataset,
            "source_id": source_id_text,
            "url": str(doc.get("url") or ""),
            "title": str(doc.get("title") or ""),
            "author": str(author).strip() if author is not None and str(author).strip() else None,
            "category": str(doc.get("category") or ""),
            "subcategory": str(doc.get("subcategory") or ""),
            "type": document_type,
            "tags": list(doc.get("tags") or []),
            "published_at": published_iso,
            "updated_at": updated_iso,
            "effective_at": effective_iso,
            "content": str(doc.get("content") or ""),
            "content_hash": content_sha256(doc.get("content")),
            "content_source": content_source,
            "attachments": list(doc.get("attachments") or []),
            "crawl": {
                "crawled_at": crawl_iso,
                "status": "success",
                "http_status": 200,
                "parser": content_source,
                "parser_version": SCHEMA_VERSION,
                "warnings": [],
            },
            "metadata": merged_metadata,
        }
    )
    # Legacy readers still expect this top-level field.
    result["crawled_at"] = crawl_iso
    return result


def validate_common_document(doc: dict[str, Any], project_root: Path | None = None) -> list[str]:
    """Return schema validation errors without requiring a third-party package."""
    errors: list[str] = []
    missing = sorted(REQUIRED_DOCUMENT_FIELDS - doc.keys())
    if missing:
        errors.append(f"missing fields: {', '.join(missing)}")
        return errors
    if doc.get("schema_version") != SCHEMA_VERSION:
        errors.append("schema_version must be 1.0")
    if doc.get("type") not in STANDARD_DOCUMENT_TYPES:
        errors.append("type is not a standard document type")
    if not str(doc.get("id") or "").startswith(f"{doc.get('source_dataset')}:"):
        errors.append("id must start with source_dataset")
    if not re.fullmatch(r"[0-9a-f]{16}", str(doc.get("slug") or "")):
        errors.append("slug must be a 16-character lowercase hex value")
    if not re.match(r"^https?://", str(doc.get("source_site") or "")):
        errors.append("source_site must be an absolute HTTP(S) URL")
    for key in ("id", "slug", "source_site", "source_dataset", "source_id", "url", "title", "category", "content", "content_hash", "content_source"):
        if not isinstance(doc.get(key), str):
            errors.append(f"{key} must be a string")
    if doc.get("author") is not None and not isinstance(doc.get("author"), str):
        errors.append("author must be a string or null")
    if doc.get("subcategory") is not None and not isinstance(doc.get("subcategory"), str):
        errors.append("subcategory must be a string or null")
    if not isinstance(doc.get("tags"), list):
        errors.append("tags must be a list")
    if not isinstance(doc.get("attachments"), list):
        errors.append("attachments must be a list")
    else:
        for index, attachment in enumerate(doc["attachments"], start=1):
            if not isinstance(attachment, dict):
                errors.append(f"attachments[{index}] must be an object")
            else:
                errors.extend(
                    f"attachments[{index}]: {error}"
                    for error in validate_attachment(attachment, project_root or Path.cwd())
                )
    crawl_info = doc.get("crawl")
    if not isinstance(crawl_info, dict):
        errors.append("crawl must be an object")
    if not isinstance(doc.get("metadata"), dict):
        errors.append("metadata must be an object")
    if doc.get("content_hash") != content_sha256(doc.get("content")):
        errors.append("content_hash does not match normalized content")
    for key in ("published_at", "updated_at", "effective_at"):
        value = doc.get(key)
        if value is not None:
            try:
                parsed = datetime.fromisoformat(str(value))
            except ValueError:
                errors.append(f"{key} must be valid ISO 8601")
            else:
                if parsed.utcoffset() != timedelta(hours=9):
                    errors.append(f"{key} must use the +09:00 offset")
    crawl_value = crawl_info.get("crawled_at") if isinstance(crawl_info, dict) else None
    try:
        crawl_datetime = datetime.fromisoformat(str(crawl_value or ""))
    except ValueError:
        errors.append("crawl.crawled_at must be valid ISO 8601")
    else:
        if crawl_datetime.utcoffset() != timedelta(hours=9):
            errors.append("crawl.crawled_at must use the +09:00 offset")
    return errors

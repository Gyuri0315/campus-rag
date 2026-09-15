"""Convert legacy crawler JSON documents to schema 1.0 (dry-run by default)."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.crawlers.common.reader import read_document  # noqa: E402
from scripts.crawlers.common.schema import content_sha256, normalize_content  # noqa: E402
from scripts.crawlers.common.storage import get_dataset_paths  # noqa: E402


DATASETS = ("ce", "pknu_notice", "pknu_student_life", "rule")


def effective_legacy_content(doc: dict[str, Any]) -> str:
    return str(doc.get("content") if doc.get("content") is not None else doc.get("page_content") or doc.get("html_text") or "")


def convert_file(path: Path, dataset: str, apply: bool) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError("document root must be an object")
    before_content = effective_legacy_content(payload)
    before_attachments = len(payload.get("attachments") or []) if isinstance(payload.get("attachments") or [], list) else 0
    before_hash = str(payload.get("content_hash") or "")
    converted = read_document(
        payload, dataset=dataset, project_root=PROJECT_ROOT, remove_redundant=True
    )
    after_content = str(converted.get("content") or "")
    after_attachments = len(converted.get("attachments") or [])
    content_preserved = normalize_content(before_content) == normalize_content(after_content)
    attachments_preserved = before_attachments == after_attachments
    hash_valid = converted.get("content_hash") == content_sha256(after_content)
    if not content_preserved or not attachments_preserved or not hash_valid:
        raise ValueError(
            f"loss check failed content={content_preserved} attachments={attachments_preserved} hash={hash_valid}"
        )

    backup = path.with_name(path.name + ".legacy.bak")
    if apply:
        if backup.exists():
            raise FileExistsError(f"backup already exists: {backup}")
        shutil.copy2(path, backup)
        handle = tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.",
            suffix=".tmp", delete=False,
        )
        temp_path = Path(handle.name)
        try:
            with handle:
                json.dump(converted, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, path)
        finally:
            temp_path.unlink(missing_ok=True)
    return {
        "path": path.as_posix(), "schema_before": payload.get("schema_version") or "legacy",
        "schema_after": converted["schema_version"], "content_preserved": content_preserved,
        "attachments_before": before_attachments, "attachments_after": after_attachments,
        "content_hash_before": before_hash or None, "content_hash_after": converted["content_hash"],
        "content_hash_changed": before_hash != converted["content_hash"],
        "backup": backup.as_posix() if apply else None,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Migrate crawler documents to schema 1.0 (dry-run by default).")
    parser.add_argument("--dataset", choices=DATASETS, required=True)
    parser.add_argument("--input-root", type=Path, default=None)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--details", action="store_true",
        help="Include every converted document in the JSON report.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.input_root or get_dataset_paths(PROJECT_ROOT, args.dataset).json
    files = sorted(root.rglob("*.json")) if root.exists() else []
    results: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    for path in files:
        try:
            results.append(convert_file(path, args.dataset, args.apply))
        except Exception as exc:
            failures.append({"path": path.as_posix(), "error": str(exc)})
    report = {
        "dataset": args.dataset, "dry_run": not args.apply, "input_root": root.as_posix(),
        "documents_before": len(files), "documents_after": len(results),
        "attachments_before": sum(item["attachments_before"] for item in results),
        "attachments_after": sum(item["attachments_after"] for item in results),
        "content_preserved": all(item["content_preserved"] for item in results),
        "content_hash_changed": sum(1 for item in results if item["content_hash_changed"]),
        "failure_count": len(failures), "failures": failures,
    }
    if args.details:
        report["documents"] = results
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

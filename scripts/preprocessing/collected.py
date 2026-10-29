"""Inspect or preprocess all collected datasets, without crawling or DB writes."""
from __future__ import annotations

import argparse
from collections import Counter
import importlib.util
import json
import os
from pathlib import Path
import shutil

from scripts.crawlers.common.storage import department_datasets, get_dataset_root
from scripts.crawlers.main.paths import is_crawler_document
from scripts.preprocessing.file_preprocessing import SUPPORTED_EXTS, run_batch
from scripts.preprocessing.preprocessing_pipeline import PROJECT_ROOT, configure_logging, run_rule_dataset

EXCLUDED = {"preprocessed", "vectorized", "vectors", "_derived", "_archive", "_runs",
            "_state", "_incoming", "_reviewed", "__pycache__"}
IMAGES = {".jpg", ".jpeg", ".jpe", ".png", ".gif", ".bmp", ".webp", ".svg", ".tif", ".tiff"}


def source_files(root: Path):
    """Skip operational/derived trees before descending, including syllabus state."""
    for directory, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in EXCLUDED and not d.startswith("."))
        for name in sorted(files):
            yield Path(directory) / name


def scopes(root: Path):
    for name in department_datasets():
        dataset = get_dataset_root(root, name)
        if (dataset / "output").is_dir():
            yield name, dataset / "output", dataset / "preprocessed"
    for name in ("pknu_main", "pknu_syllabus", "rule"):
        dataset = root / "files" / name
        if dataset.is_dir():
            source = dataset / "output" if name == "rule" else dataset
            output = dataset / "_derived" / "all_collected" / "preprocessed" if name == "pknu_main" else dataset / "preprocessed"
            yield name, source, output


def inspect(root: Path) -> dict:
    result = {}
    for name, source, _ in scopes(root):
        counts = Counter()
        for path in source_files(source):
            ext = path.suffix.lower()
            if ext == ".json":
                kind = "document_json" if is_crawler_document(path) else "other_json"
            elif ext in {".html", ".htm"}:
                kind = "html_snapshot"
            elif ext in IMAGES:
                kind = "image_separate_review"
            elif ext in SUPPORTED_EXTS:
                kind = "extractable"
            else:
                kind = "unsupported"
            counts[f"{kind}:{ext}"] += 1
        result[name] = dict(sorted(counts.items()))
    return result


def run(root: Path, *, dry_run: bool, pdf_ocr: str) -> None:
    for name, source, output in scopes(root):
        if name == "rule":
            run_rule_dataset(changed_only=True, dry_run=dry_run, chunk_size=900,
                             chunk_overlap=120, file_exts=None)
            continue
        groups: dict[tuple[Path, str], list[Path]] = {}
        for path in source_files(source):
            ext = path.suffix.lower()
            if ext not in SUPPORTED_EXTS:
                continue
            if ext == ".json" and not is_crawler_document(path):
                continue
            # Preserve category roots and join attachments to their sibling JSON.
            relative = path.relative_to(source)
            kind = "json" if ext == ".json" else "files"
            if kind in relative.parts:
                index = relative.parts.index(kind)
                input_root = source.joinpath(*relative.parts[:index + 1])
            else:
                input_root = source
            groups.setdefault((input_root, kind), []).append(path)
        for (input_root, kind), targets in sorted(groups.items()):
            relative = input_root.relative_to(source)
            if input_root.name == kind:
                relative = relative.parent
            run_batch(input_root=input_root, output_root=output / kind / relative,
                      output_json_root=input_root if kind == "json" else input_root.parent / "json",
                      project_root=root, failed_from_log=None, dry_run=dry_run,
                      chunk_size=900, chunk_overlap=120, pdf_ocr_mode=pdf_ocr,
                      ocr_language="kor+eng", ocr_dpi=200,
                      layout="flat" if kind == "json" else "by_ext",
                      changed_only=True, target_files=targets)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="Write chunks; default is read-only inventory.")
    parser.add_argument("--dry-run", action="store_true", help="List processing targets without writing chunks.")
    parser.add_argument("--pdf-ocr", choices=("auto", "never", "always"), default="auto")
    parser.add_argument("--details", action="store_true", help="Include counts for each dataset in the inventory.")
    args = parser.parse_args()
    if args.run or args.dry_run:
        configure_logging()
        run(PROJECT_ROOT, dry_run=args.dry_run, pdf_ocr=args.pdf_ocr)
    else:
        datasets = inspect(PROJECT_ROOT)
        totals = Counter()
        for counts in datasets.values():
            totals.update(counts)
        inventory = {"dataset_count": len(datasets), "totals": dict(sorted(totals.items()))}
        if args.details:
            inventory["datasets"] = datasets
        print(json.dumps({**inventory, "dependencies": {
            module: importlib.util.find_spec(module) is not None
            for module in ("fitz", "docx", "xlrd", "pptx", "hwp5", "bs4")
        }, "tesseract_on_path": shutil.which("tesseract")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

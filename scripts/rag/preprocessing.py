"""Dataset-aware preprocessing entrypoint for RAG pipelines.

This script centralizes RAG preprocessing execution across crawled datasets.
The extraction implementation is currently shared with scripts.ce.preprocessing
for compatibility, while dataset routing and source-scope orchestration live here.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.ce.preprocessing import (  # noqa: E402
    DEFAULT_CHUNK_OVERLAP,
    DEFAULT_CHUNK_SIZE,
    DEFAULT_OCR_DPI,
    DEFAULT_OCR_LANGUAGE,
    DEFAULT_PDF_OCR_MODE,
    configure_logging,
    run_batch,
)


DATASET_PATHS = {
    "ce": {
        "files_input_root": PROJECT_ROOT / "files" / "ce" / "output" / "files",
        "files_output_root": PROJECT_ROOT / "files" / "ce" / "preprocessed" / "files",
        "json_input_root": PROJECT_ROOT / "files" / "ce" / "output" / "json",
        "json_output_root": PROJECT_ROOT / "files" / "ce" / "preprocessed" / "json",
        "output_json_root": PROJECT_ROOT / "files" / "ce" / "output" / "json",
    },
    "pknu_notice": {
        "files_input_root": PROJECT_ROOT / "files" / "pknu_notice" / "output" / "files",
        "files_output_root": PROJECT_ROOT / "files" / "pknu_notice" / "preprocessed" / "files",
        "json_input_root": PROJECT_ROOT / "files" / "pknu_notice" / "output" / "json",
        "json_output_root": PROJECT_ROOT / "files" / "pknu_notice" / "preprocessed" / "json",
        "output_json_root": PROJECT_ROOT / "files" / "pknu_notice" / "output" / "json",
    },
    "pknu_student_life": {
        "files_input_root": PROJECT_ROOT / "files" / "pknu_student_life" / "output" / "files",
        "files_output_root": PROJECT_ROOT / "files" / "pknu_student_life" / "preprocessed" / "files",
        "json_input_root": PROJECT_ROOT / "files" / "pknu_student_life" / "output" / "json",
        "json_output_root": PROJECT_ROOT / "files" / "pknu_student_life" / "preprocessed" / "json",
        "output_json_root": PROJECT_ROOT / "files" / "pknu_student_life" / "output" / "json",
    },
}


def build_specs(args: argparse.Namespace) -> list[tuple[Path, Path, Path, str]]:
    dataset_paths = DATASET_PATHS[args.dataset]
    has_path_override = bool(args.input_root or args.output_root or args.output_json_root)
    if has_path_override:
        return [
            (
                args.input_root or dataset_paths["files_input_root"],
                args.output_root or dataset_paths["files_output_root"],
                args.output_json_root or dataset_paths["output_json_root"],
                args.layout or "by_ext",
            )
        ]

    scopes = ["json", "files"] if args.source_scope == "all" else [args.source_scope]
    specs = []
    for scope in scopes:
        specs.append(
            (
                dataset_paths[f"{scope}_input_root"],
                dataset_paths[f"{scope}_output_root"],
                dataset_paths["output_json_root"],
                args.layout or ("flat" if scope == "json" else "by_ext"),
            )
        )
    return specs


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Preprocess crawled dataset outputs into RAG chunk JSON files."
    )
    parser.add_argument("--dataset", choices=sorted(DATASET_PATHS), default="ce")
    parser.add_argument(
        "--source-scope",
        choices=["files", "json", "all"],
        default="all",
        help="Dataset preset source to preprocess.",
    )
    parser.add_argument("--input-root", type=Path, default=None)
    parser.add_argument("--output-root", type=Path, default=None)
    parser.add_argument("--output-json-root", type=Path, default=None)
    parser.add_argument("--failed-from-log", type=Path, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE)
    parser.add_argument("--chunk-overlap", type=int, default=DEFAULT_CHUNK_OVERLAP)
    parser.add_argument(
        "--pdf-ocr",
        choices=["auto", "never", "always"],
        default=DEFAULT_PDF_OCR_MODE,
    )
    parser.add_argument("--ocr-language", default=DEFAULT_OCR_LANGUAGE)
    parser.add_argument("--ocr-dpi", type=int, default=DEFAULT_OCR_DPI)
    parser.add_argument("--layout", choices=["by_ext", "flat"], default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    configure_logging()

    for input_root, output_root, output_json_root, layout in build_specs(args):
        run_batch(
            input_root=input_root,
            output_root=output_root,
            output_json_root=output_json_root,
            project_root=PROJECT_ROOT,
            failed_from_log=args.failed_from_log.resolve() if args.failed_from_log else None,
            dry_run=args.dry_run,
            chunk_size=args.chunk_size,
            chunk_overlap=args.chunk_overlap,
            pdf_ocr_mode=args.pdf_ocr,
            ocr_language=args.ocr_language,
            ocr_dpi=args.ocr_dpi,
            layout=layout,
        )


if __name__ == "__main__":
    main()

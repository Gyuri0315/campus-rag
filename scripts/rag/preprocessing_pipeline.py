"""Run preprocessing for all crawled RAG datasets.

This module only covers preprocessing. Vectorization, database loading, and
priority updates stay as separate steps.
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.rag.file_preprocessing import (  # noqa: E402
    DEFAULT_CHUNK_OVERLAP,
    DEFAULT_CHUNK_SIZE,
    DEFAULT_OCR_DPI,
    DEFAULT_OCR_LANGUAGE,
    DEFAULT_PDF_OCR_MODE,
    SUPPORTED_EXTS,
    parse_file_exts,
    run_batch as run_file_preprocessing,
)
from scripts.rule.preprocessing import (  # noqa: E402
    SUPPORTED_ATTACHMENT_EXTS,
    run_batch as run_rule_preprocessing,
)

LOG_DIR = PROJECT_ROOT / "logs"
LOG_FILE = LOG_DIR / "preprocessing_pipeline.log"
DATASET_CHOICES = ("ce", "pknu_notice", "pknu_student_life", "rule", "all")
TargetMap = dict[str, dict[str, list[Path]]]

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class WebDataset:
    name: str
    output_root: Path
    preprocessed_root: Path

    @property
    def json_root(self) -> Path:
        return self.output_root / "json"

    @property
    def files_root(self) -> Path:
        return self.output_root / "files"

    @property
    def preprocessed_json_root(self) -> Path:
        return self.preprocessed_root / "json"

    @property
    def preprocessed_files_root(self) -> Path:
        return self.preprocessed_root / "files"


WEB_DATASETS = {
    "ce": WebDataset(
        name="ce",
        output_root=PROJECT_ROOT / "files" / "ce" / "output",
        preprocessed_root=PROJECT_ROOT / "files" / "ce" / "preprocessed",
    ),
    "pknu_notice": WebDataset(
        name="pknu_notice",
        output_root=PROJECT_ROOT / "files" / "pknu_notice" / "output",
        preprocessed_root=PROJECT_ROOT / "files" / "pknu_notice" / "preprocessed",
    ),
    "pknu_student_life": WebDataset(
        name="pknu_student_life",
        output_root=PROJECT_ROOT / "files" / "pknu_student_life" / "output",
        preprocessed_root=PROJECT_ROOT / "files" / "pknu_student_life" / "preprocessed",
    ),
}


def configure_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(LOG_FILE, encoding="utf-8"),
        ],
    )


def selected_datasets(dataset: str) -> list[str]:
    if dataset == "all":
        return ["ce", "pknu_notice", "pknu_student_life", "rule"]
    return [dataset]


def run_web_dataset(
    dataset: WebDataset,
    *,
    changed_only: bool,
    dry_run: bool,
    chunk_size: int,
    chunk_overlap: int,
    pdf_ocr_mode: str,
    ocr_language: str,
    ocr_dpi: int,
    file_exts: set[str] | None,
    targets: dict[str, list[Path]] | None = None,
) -> None:
    log.info("[%s] JSON preprocessing", dataset.name)
    run_file_preprocessing(
        input_root=dataset.json_root,
        output_root=dataset.preprocessed_json_root,
        output_json_root=dataset.json_root,
        project_root=PROJECT_ROOT,
        failed_from_log=None,
        dry_run=dry_run,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        pdf_ocr_mode=pdf_ocr_mode,
        ocr_language=ocr_language,
        ocr_dpi=ocr_dpi,
        layout="flat",
        file_exts=None,
        changed_only=changed_only,
        target_files=(targets or {}).get("json"),
    )

    log.info("[%s] attachment preprocessing", dataset.name)
    run_file_preprocessing(
        input_root=dataset.files_root,
        output_root=dataset.preprocessed_files_root,
        output_json_root=dataset.json_root,
        project_root=PROJECT_ROOT,
        failed_from_log=None,
        dry_run=dry_run,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        pdf_ocr_mode=pdf_ocr_mode,
        ocr_language=ocr_language,
        ocr_dpi=ocr_dpi,
        layout="by_ext",
        file_exts=file_exts,
        changed_only=changed_only,
        target_files=(targets or {}).get("files"),
    )


def run_rule_dataset(
    *,
    changed_only: bool,
    dry_run: bool,
    chunk_size: int,
    chunk_overlap: int,
    file_exts: set[str] | None,
    source_scope: str = "all",
    targets: dict[str, list[Path]] | None = None,
) -> None:
    log.info("[rule] preprocessing")
    target_tasks = None
    if targets is not None:
        target_tasks = []
        for path in targets.get("json", []):
            target_tasks.append(("json", path, PROJECT_ROOT / "files" / "rule" / "output" / "json"))
        for path in targets.get("html", []):
            target_tasks.append(("html", path, PROJECT_ROOT / "files" / "rule" / "output" / "html"))
        for path in targets.get("files", []):
            target_tasks.append(("file", path, PROJECT_ROOT / "files" / "rule" / "output" / "files"))
    run_rule_preprocessing(
        json_root=PROJECT_ROOT / "files" / "rule" / "output" / "json",
        html_root=PROJECT_ROOT / "files" / "rule" / "output" / "html",
        files_root=PROJECT_ROOT / "files" / "rule" / "output" / "files",
        output_root=PROJECT_ROOT / "files" / "rule" / "preprocessed",
        source_scope=source_scope,
        failed_from_log=None,
        dry_run=dry_run,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        file_exts=file_exts,
        changed_only=changed_only,
        target_tasks=target_tasks,
    )


def run_pipeline(
    *,
    dataset: str = "all",
    changed_only: bool = False,
    dry_run: bool = False,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
    pdf_ocr_mode: str = DEFAULT_PDF_OCR_MODE,
    ocr_language: str = DEFAULT_OCR_LANGUAGE,
    ocr_dpi: int = DEFAULT_OCR_DPI,
    file_exts: set[str] | None = None,
    targets: TargetMap | None = None,
) -> None:
    for name in selected_datasets(dataset):
        dataset_targets = (targets or {}).get(name)
        if name == "rule":
            rule_exts = None if file_exts is None else file_exts & SUPPORTED_ATTACHMENT_EXTS
            if file_exts is not None and not rule_exts:
                for source_scope in ("json", "html"):
                    run_rule_dataset(
                        changed_only=changed_only,
                        dry_run=dry_run,
                        chunk_size=chunk_size,
                        chunk_overlap=chunk_overlap,
                        file_exts=None,
                        source_scope=source_scope,
                        targets=dataset_targets,
                    )
            else:
                run_rule_dataset(
                    changed_only=changed_only,
                    dry_run=dry_run,
                    chunk_size=chunk_size,
                    chunk_overlap=chunk_overlap,
                    file_exts=rule_exts,
                    targets=dataset_targets,
                )
        else:
            run_web_dataset(
                WEB_DATASETS[name],
                changed_only=changed_only,
                dry_run=dry_run,
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
                pdf_ocr_mode=pdf_ocr_mode,
                ocr_language=ocr_language,
                ocr_dpi=ocr_dpi,
                file_exts=file_exts,
                targets=dataset_targets,
            )


def build_parser(description: str | None = None) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=description or "Run preprocessing for crawled RAG datasets."
    )
    parser.add_argument("--dataset", choices=DATASET_CHOICES, default="all")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--changed-only",
        action="store_true",
        help="Skip outputs that are already newer than their source files.",
    )
    parser.add_argument("--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE)
    parser.add_argument("--chunk-overlap", type=int, default=DEFAULT_CHUNK_OVERLAP)
    parser.add_argument(
        "--pdf-ocr",
        choices=["auto", "never", "always"],
        default=DEFAULT_PDF_OCR_MODE,
        help="PDF OCR fallback mode for common file preprocessing.",
    )
    parser.add_argument("--ocr-language", default=DEFAULT_OCR_LANGUAGE)
    parser.add_argument("--ocr-dpi", type=int, default=DEFAULT_OCR_DPI)
    parser.add_argument(
        "--file-ext",
        "--file-exts",
        dest="file_exts",
        nargs="+",
        default=None,
        help="Limit attachment preprocessing to extensions such as pdf hwp xlsx.",
    )
    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.file_exts = parse_file_exts(args.file_exts)
    unsupported = sorted((args.file_exts or set()) - SUPPORTED_EXTS)
    if unsupported:
        parser.error(
            "unsupported --file-ext value(s): "
            + ", ".join(unsupported)
            + ". Supported: "
            + ", ".join(sorted(SUPPORTED_EXTS))
        )
    return args


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    configure_logging()
    run_pipeline(
        dataset=args.dataset,
        changed_only=args.changed_only,
        dry_run=args.dry_run,
        chunk_size=args.chunk_size,
        chunk_overlap=args.chunk_overlap,
        pdf_ocr_mode=args.pdf_ocr,
        ocr_language=args.ocr_language,
        ocr_dpi=args.ocr_dpi,
        file_exts=args.file_exts,
    )


if __name__ == "__main__":
    main()

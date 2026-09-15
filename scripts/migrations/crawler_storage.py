"""Plan or apply non-destructive crawler state-path migrations."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.crawlers.common.storage import (  # noqa: E402
    empty_state, get_dataset_paths, load_state_with_migration,
    save_state_atomic, state_migration_plan,
)


DATASETS = ("ce", "pknu_notice", "pknu_student_life", "rule")
LEGACY_FILES = {
    "ce": (PROJECT_ROOT / "state.json",),
    "pknu_notice": (PROJECT_ROOT / "state_pknu_notice.json",),
    "pknu_student_life": (PROJECT_ROOT / "state_pknu_student_life.json",),
    "rule": (),
}
LEGACY_KINDS = {"pknu_notice": "posts"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Plan crawler state path migration (dry-run by default).")
    parser.add_argument("--dataset", choices=(*DATASETS, "all"), default="all")
    parser.add_argument("--apply", action="store_true", help="Apply safe state copies/creation; never moves outputs.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    selected = DATASETS if args.dataset == "all" else (args.dataset,)
    report: list[dict[str, object]] = []
    for dataset in selected:
        paths = get_dataset_paths(PROJECT_ROOT, dataset)
        legacy = LEGACY_FILES[dataset]
        plan = state_migration_plan(paths, legacy)
        entry: dict[str, object] = {"dataset": dataset, "dry_run": not args.apply, "actions": plan}
        if args.apply and not paths.state.exists():
            if any(path.exists() for path in legacy):
                _, origin = load_state_with_migration(
                    paths, legacy_paths=legacy, legacy_kind=LEGACY_KINDS.get(dataset, "items")
                )
                entry["result"] = origin
            else:
                save_state_atomic(paths.state, empty_state(dataset), dataset)
                entry["result"] = "created"
        elif args.apply:
            entry["result"] = "skipped_target_exists"
        report.append(entry)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

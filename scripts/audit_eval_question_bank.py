"""Audit the balanced draft evaluation question bank."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.build_eval_question_bank import OUTPUT_PATH, TARGET_COUNTS, validate_rows


def audit(path: Path) -> list[dict]:
    rows = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    validate_rows(rows)
    return rows


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path, nargs="?", default=OUTPUT_PATH)
    args = parser.parse_args()
    rows = audit(args.path)
    counts = Counter(row["case_type"] for row in rows)
    print(f"PASS: {args.path} ({len(rows)} questions)")
    for case_type, target in TARGET_COUNTS.items():
        print(f"- {case_type}: {counts[case_type]}/{target}")
    origins = Counter(row["origin"] for row in rows)
    print(f"- origin: existing={origins['existing']} new={origins['new']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

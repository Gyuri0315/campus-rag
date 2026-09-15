"""Report regression label progress and validate rows marked ready."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.eval_ask import _validate_structured_case  # noqa: E402


DEFAULT_PATH = PROJECT_ROOT / "eval" / "cases" / "regression.jsonl"
LABEL_STATUSES = {"needs_review", "source_verified", "label_verified", "ready"}


def audit(path: Path) -> tuple[list[dict], Counter]:
    rows = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    ids = [str(row.get("id") or "") for row in rows]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate regression ids")
    for index, row in enumerate(rows, start=1):
        status = row.get("label_status")
        if status not in LABEL_STATUSES:
            raise ValueError(f"{path}:{index}: invalid label_status {status!r}")
        if status == "ready":
            _validate_structured_case(row, location=f"{path}:{index}")
    return rows, Counter(str(row["label_status"]) for row in rows)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path, nargs="?", default=DEFAULT_PATH)
    args = parser.parse_args()
    rows, counts = audit(args.path)
    print(f"PASS: {args.path} ({len(rows)} questions)")
    for status in ("needs_review", "source_verified", "label_verified", "ready"):
        print(f"- {status}: {counts[status]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

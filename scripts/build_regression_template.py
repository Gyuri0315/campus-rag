"""Replace regression.jsonl with label templates from the 100-question bank."""

from __future__ import annotations

import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
QUESTION_BANK_PATH = PROJECT_ROOT / "eval" / "drafts" / "question_bank_100.jsonl"
OUTPUT_PATH = PROJECT_ROOT / "eval" / "cases" / "regression.jsonl"


def build_template_rows() -> list[dict]:
    questions = [
        json.loads(line)
        for line in QUESTION_BANK_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    rows: list[dict] = []
    for item in questions:
        case_id = str(item["id"])
        if case_id.startswith("draft_"):
            case_id = case_id.removeprefix("draft_")
        rows.append(
            {
                "id": case_id,
                "question": item["question"],
                "category": item["case_type"],
                "case_type": item["case_type"],
                "answerable": None,
                "expected_source": {
                    "dataset": [],
                    "title_keywords": [],
                    "allowed_urls": [],
                    "source_ids": [],
                },
                "required_facts": [],
                "forbidden_claims": [],
                "expected_no_info": None,
                "tags": [],
                "scenario_tags": item["scenario_tags"],
                "difficulty": item["difficulty"],
                "label_status": "needs_review",
                "origin": item["origin"],
                "expected_behavior": "",
                "review_notes": "",
            }
        )
    return rows


def main() -> int:
    rows = build_template_rows()
    if len(rows) != 100:
        raise ValueError(f"expected 100 template rows, got {len(rows)}")
    ids = [row["id"] for row in rows]
    if len(ids) != len(set(ids)):
        raise ValueError("template contains duplicate ids")
    OUTPUT_PATH.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
        newline="\n",
    )
    print(f"saved={OUTPUT_PATH} templates={len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

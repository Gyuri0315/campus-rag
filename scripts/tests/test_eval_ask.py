from __future__ import annotations

import unittest
from pathlib import Path

from scripts.audit_eval_question_bank import audit
from scripts.audit_regression_labels import audit as audit_regression
from scripts.build_eval_question_bank import TARGET_COUNTS, build_rows, validate_rows
from scripts.build_regression_template import build_template_rows
from scripts.eval_ask import (
    DEFAULT_OUTPUT_PATH,
    DEFAULT_QUESTIONS_PATH,
    _is_structured_case,
    _read_jsonl,
    _validate_structured_case,
    _validate_unique_ids,
)


def valid_case() -> dict:
    return {
        "id": "rule_test_001",
        "question": "질문",
        "category": "규정",
        "answerable": True,
        "expected_source": {
            "dataset": ["rule"],
            "title_keywords": ["학칙"],
            "allowed_urls": [],
            "source_ids": [],
        },
        "required_facts": [
            {"id": "fact_1", "description": "사실", "keywords": ["키워드"]}
        ],
        "forbidden_claims": [],
        "expected_no_info": False,
        "tags": ["테스트"],
        "difficulty": "easy",
    }


class EvalSchemaTests(unittest.TestCase):
    def test_accepts_answerable_case(self) -> None:
        _validate_structured_case(valid_case(), location="case")

    def test_accepts_unanswerable_case(self) -> None:
        case = valid_case()
        case.update(
            answerable=False,
            expected_no_info=True,
            expected_source=None,
            required_facts=[],
        )
        _validate_structured_case(case, location="case")

    def test_rejects_inconsistent_no_info(self) -> None:
        case = valid_case()
        case["expected_no_info"] = True
        with self.assertRaisesRegex(ValueError, "inverse of answerable"):
            _validate_structured_case(case, location="case")

    def test_rejects_duplicate_case_ids(self) -> None:
        with self.assertRaisesRegex(ValueError, "duplicate case ids"):
            _validate_unique_ids([valid_case(), valid_case()], location="cases")

    def test_legacy_question_remains_readable(self) -> None:
        self.assertFalse(_is_structured_case({"question": "기존 질문", "category": "규정"}))

    def test_repository_eval_files_are_readable(self) -> None:
        smoke = _read_jsonl(Path("eval/cases/smoke.jsonl"))
        challenge = _read_jsonl(Path("eval/cases/challenge.jsonl"))
        regression, statuses = audit_regression(Path("eval/cases/regression.jsonl"))
        self.assertEqual(len(smoke), 8)
        self.assertEqual(len(regression), 100)
        self.assertEqual(len(challenge), 5)
        self.assertTrue(all(case.get("id") for case in smoke + challenge))
        self.assertGreaterEqual(len(regression), len(smoke))
        self.assertEqual(statuses["needs_review"], 100)
        self.assertEqual(DEFAULT_QUESTIONS_PATH, Path("eval/cases/smoke.jsonl"))
        self.assertEqual(DEFAULT_OUTPUT_PATH, Path("eval/results/smoke.jsonl"))

    def test_balanced_draft_question_bank(self) -> None:
        rows = audit(Path("eval/drafts/question_bank_100.jsonl"))
        self.assertEqual(len(rows), 100)
        self.assertEqual(
            {case_type: sum(row["case_type"] == case_type for row in rows) for case_type in TARGET_COUNTS},
            TARGET_COUNTS,
        )
        self.assertTrue(all(row["label_status"] == "needs_review" for row in rows))

    def test_builder_source_matches_generated_bank(self) -> None:
        rows = build_rows()
        validate_rows(rows)
        generated = audit(Path("eval/drafts/question_bank_100.jsonl"))
        self.assertEqual(rows, generated)

    def test_regression_template_matches_question_bank(self) -> None:
        expected = build_template_rows()
        actual, _ = audit_regression(Path("eval/cases/regression.jsonl"))
        self.assertEqual(expected, actual)
        self.assertTrue(all(row["answerable"] is None for row in actual))
        self.assertTrue(all(row["expected_no_info"] is None for row in actual))


if __name__ == "__main__":
    unittest.main()

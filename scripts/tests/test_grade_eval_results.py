from __future__ import annotations

import json
import unittest
from types import SimpleNamespace

from scripts.grade_eval_results import confirm_forbidden_violations, verify_forbidden_evidence


class _FakeClient:
    """Returns a fixed confirm-pass answer and records how often it was asked."""

    def __init__(self, asserts):
        self.calls = 0
        outer = self

        class Completions:
            def create(self, **_kwargs):
                outer.calls += 1
                content = json.dumps({"asserts": asserts, "reason": "r"})
                return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])

        self.chat = SimpleNamespace(completions=Completions())


class VerifyEvidenceTests(unittest.TestCase):
    ANSWER = "**군휴학**: 통산 6개 학기(3년)를 초과할 수 없습니다[5]."

    def test_quote_matches_despite_markdown_and_citations(self):
        items = [{"index": 0, "verdict": "violated", "evidence": "군휴학: 통산 6개 학기(3년)를 초과할 수 없습니다."}]
        verify_forbidden_evidence(items, self.ANSWER)
        self.assertEqual("violated", items[0]["verdict"])

    def test_quote_not_in_answer_is_downgraded(self):
        items = [{"index": 0, "verdict": "violated", "evidence": "군휴학은 제한이 없습니다"}]
        verify_forbidden_evidence(items, self.ANSWER)
        self.assertEqual(("ok", "violated", "evidence_not_in_answer"),
                         (items[0]["verdict"], items[0]["original_verdict"], items[0]["downgraded"]))

    def test_missing_quote_is_downgraded_and_ok_is_untouched(self):
        items = [{"index": 0, "verdict": "hedged", "evidence": ""}, {"index": 1, "verdict": "ok"}]
        verify_forbidden_evidence(items, self.ANSWER)
        self.assertEqual("evidence_missing", items[0]["downgraded"])
        self.assertEqual({"index": 1, "verdict": "ok"}, items[1])


class ConfirmViolationTests(unittest.TestCase):
    def test_rejected_confirmation_downgrades(self):
        client = _FakeClient(asserts=False)
        items = [{"index": 0, "verdict": "violated", "evidence": "졸업할 수 없습니다"}]
        confirm_forbidden_violations(client, "m", items, ["졸업 가능하다고 안내"], 5)
        self.assertEqual(("ok", "confirm_pass_rejected"), (items[0]["verdict"], items[0]["downgraded"]))

    def test_confirmed_violation_stays_and_only_violations_are_checked(self):
        client = _FakeClient(asserts=True)
        items = [{"index": 0, "verdict": "violated", "evidence": "x"}, {"index": 1, "verdict": "hedged"},
                 {"index": 2, "verdict": "ok"}]
        confirm_forbidden_violations(client, "m", items, ["a", "b", "c"], 5)
        self.assertEqual("violated", items[0]["verdict"])
        self.assertEqual(1, client.calls)


if __name__ == "__main__":
    unittest.main()

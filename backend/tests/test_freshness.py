"""freshness: semester detection and the stale/graduate/schedule adjustments."""

import unittest
from datetime import date

from app.freshness import (
    CURRENT_TERM_BONUS,
    GRADUATE_PENALTY,
    SCHEDULE_SEED_SLOTS,
    STALE_PENALTY_CAP,
    STALE_PENALTY_PER_TERM,
    current_term,
    current_term_label,
    document_term,
    freshness_adjustment,
    is_current_term_notice,
    seed_slots,
    term_index,
)

TODAY = date(2026, 10, 14)  # 2026-2학기
NOTICE = "match_pknu_notice_documents"


def row(title="", **metadata):
    return {"title": title, "metadata": {"doc_title": title, **metadata}, "content": ""}


class DocumentTermTests(unittest.TestCase):
    def test_title_term_wins_over_posting_date(self):
        self.assertEqual(term_index(2026, 2), document_term(row("2026-2학기 학부 본수강신청 안내", date="2026-07-01")))
        self.assertEqual(term_index(2025, 1), document_term(row("2025학년도 제1학기 등록 안내")))

    def test_posting_date_then_bare_title_year(self):
        self.assertEqual(term_index(2025, 1), document_term(row("수강신청 실시 안내", date="2025-02-04")))
        self.assertEqual(term_index(2026, 2), document_term(row("2026학년도 학사일정 안내")))

    def test_unknown_when_nothing_dated(self):
        self.assertIsNone(document_term(row("휴ㆍ복학")))

    def test_current_term(self):
        self.assertEqual(term_index(2026, 2), current_term(TODAY))
        self.assertEqual(term_index(2027, 1), current_term(date(2027, 3, 2)))
        self.assertEqual("2026학년도 2학기", current_term_label(TODAY))


class AdjustmentTests(unittest.TestCase):
    def test_older_notice_loses_per_term_up_to_cap(self):
        one_behind = freshness_adjustment(row("2026-1학기 안내"), NOTICE, "졸업 요건", TODAY)
        self.assertAlmostEqual(-STALE_PENALTY_PER_TERM, one_behind)
        ancient = freshness_adjustment(row("2019-1학기 안내"), NOTICE, "졸업 요건", TODAY)
        self.assertAlmostEqual(-STALE_PENALTY_CAP, ancient)

    def test_rules_are_never_stale(self):
        self.assertEqual(0.0, freshness_adjustment(row("2019-1학기 운영 지침"), "match_rule_documents", "졸업 요건", TODAY))

    def test_question_naming_a_year_turns_staleness_off(self):
        self.assertEqual(0.0, freshness_adjustment(row("2024-1학기 안내"), NOTICE, "2024년 수강신청 기간", TODAY))

    def test_schedule_question_boosts_current_term_and_penalizes_old_harder(self):
        current = freshness_adjustment(row("2026-2학기 학부 본수강신청 안내"), NOTICE, "수강신청 기간", TODAY)
        self.assertAlmostEqual(CURRENT_TERM_BONUS, current)
        old = freshness_adjustment(row("2025-1학기 수강신청"), NOTICE, "수강신청 기간", TODAY)
        plain_old = freshness_adjustment(row("2025-1학기 수강신청"), NOTICE, "수강신청 방법", TODAY)
        self.assertLess(old, plain_old)

    def test_graduate_sources_penalized_unless_asked(self):
        grad = row("2025-1학기 일반대학원 수강신청 실시 안내", subcategory="대학원공지")
        undergrad_q = freshness_adjustment(grad, "match_rag_documents", "수강신청 방법", TODAY)
        grad_q = freshness_adjustment(grad, "match_rag_documents", "대학원 수강신청 방법", TODAY)
        self.assertAlmostEqual(grad_q - GRADUATE_PENALTY, undergrad_q)

    def test_seed_slots_only_for_notice_tables_on_schedule_questions(self):
        self.assertEqual(SCHEDULE_SEED_SLOTS, seed_slots(NOTICE, "수강신청 언제야"))
        self.assertEqual(1, seed_slots(NOTICE, "수강신청 방법"))
        self.assertEqual(1, seed_slots("match_rule_documents", "수강신청 언제야"))


class CurrentTermNoticeTests(unittest.TestCase):
    def test_current_and_future_terms_count_past_does_not(self):
        self.assertTrue(is_current_term_notice(row("2026-2학기 학부 본수강신청 안내"), TODAY))
        self.assertTrue(is_current_term_notice(row("2027-1학기 예비수강신청 안내"), TODAY))
        self.assertFalse(is_current_term_notice(row("2026-1학기 본수강신청 안내"), TODAY))
        self.assertFalse(is_current_term_notice(row("휴ㆍ복학"), TODAY))


if __name__ == "__main__":
    unittest.main()

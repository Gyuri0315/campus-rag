from __future__ import annotations

import unittest

from app.excerpts import extract_relevant_excerpt


class ExtractRelevantExcerptTests(unittest.TestCase):
    def test_uses_the_sentence_cited_for_the_source(self) -> None:
        content = (
            "수강 신청 변경 안내입니다. "
            "졸업을 위해서는 전공 필수 과목을 모두 이수해야 합니다. "
            "장학금 신청은 학생 포털에서 진행합니다. "
            "기숙사 입사 일정은 별도로 공지합니다."
        )

        excerpt = extract_relevant_excerpt(
            content,
            question="졸업 요건을 알려주세요",
            answer="전공 필수 과목을 모두 이수해야 합니다 [1]. 장학금은 포털에서 신청합니다 [2].",
            source_index=1,
            max_chars=70,
        )

        self.assertIn("전공 필수", excerpt)
        self.assertNotIn("기숙사", excerpt)
        self.assertLessEqual(len(excerpt), 70)

    def test_falls_back_to_the_question_when_source_is_not_cited(self) -> None:
        content = (
            "도서관 운영 시간은 오전 9시부터입니다. "
            "복수전공 신청 기간은 3월 2일부터 3월 8일까지입니다. "
            "학생증 재발급은 행정실에서 처리합니다."
        )

        excerpt = extract_relevant_excerpt(
            content,
            question="복수전공 신청 기간이 언제인가요?",
            answer="관련 내용을 확인했습니다.",
            source_index=1,
            max_chars=65,
        )

        self.assertIn("복수전공 신청 기간", excerpt)
        self.assertLessEqual(len(excerpt), 65)

    def test_returns_short_content_without_ellipsis(self) -> None:
        excerpt = extract_relevant_excerpt(
            "짧은 원문입니다.",
            question="원문",
            answer="답변 [1].",
            source_index=1,
            max_chars=100,
        )

        self.assertEqual(excerpt, "짧은 원문입니다.")


if __name__ == "__main__":
    unittest.main()

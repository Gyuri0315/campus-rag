"""Popular-question ranking: only questions several different users asked."""

import unittest

from app.routers.popular import MIN_DISTINCT_USERS, rank_popular


class RankPopularTests(unittest.TestCase):
    def test_needs_distinct_users_not_repeats(self):
        rows = [{"content": "내 학번 2021xxxx 성적 왜 이래", "user_id": "u1"}] * 10
        rows += [{"content": f"휴학 신청 방법{'?' * i}", "user_id": f"u{i}"} for i in range(MIN_DISTINCT_USERS)]
        result = rank_popular(rows)
        self.assertEqual([item["question"] for item in result], ["휴학 신청 방법"])
        self.assertEqual(result[0]["askers"], MIN_DISTINCT_USERS)

    def test_ranked_by_askers_and_long_questions_skipped(self):
        rows = []
        for question, n in (("졸업 요건", 5), ("장학금 종류", 4), ("가" * 100, 9)):
            rows += [{"content": question, "user_id": f"{question}-{i}"} for i in range(n)]
        self.assertEqual([item["question"] for item in rank_popular(rows)], ["졸업 요건", "장학금 종류"])


if __name__ == "__main__":
    unittest.main()

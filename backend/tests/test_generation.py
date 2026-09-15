from __future__ import annotations

import unittest

from app.generation import build_user_message, load_system_prompt


class GenerationPromptTests(unittest.TestCase):
    def test_prompt_prefers_partial_grounded_answer_over_no_info(self) -> None:
        message = build_user_message(
            question="장학금 신청 방법을 알려줘",
            rows=[
                {
                    "content": "장학금은 학생포털에서 신청합니다.",
                    "metadata": {"doc_title": "장학금 안내"},
                    "similarity": 0.42,
                }
            ],
            max_chars_per_chunk=500,
        )

        self.assertIn("확인 가능한 범위까지 답하세요", message)
        self.assertIn("핵심에 답할 근거가 참고 자료에 전혀 없을 때만", message)

    def test_system_prompt_uses_no_info_only_when_no_core_evidence_exists(self) -> None:
        prompt = load_system_prompt()

        self.assertIn("관련된 내용이 하나라도 있으면", prompt)
        self.assertIn("핵심에 답할 근거가 참고 자료에 전혀 없을 때만", prompt)


if __name__ == "__main__":
    unittest.main()

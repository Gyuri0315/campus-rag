"""Admin dashboard: aggregation logic and the admin allowlist gate (no live DB)."""

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient

from app.deps import get_state, require_admin, require_user
from app.routers.admin import build_dashboard, normalize_question, router as admin_router


def feedback(id_, rating, chat, answer_at, *, reasons=None, comment=None, answer="답변"):
    return {
        "id": id_, "rating": rating, "reasons": reasons or [], "comment": comment,
        "created_at": f"2026-10-02T10:{id_[-1]}0:00+00:00",
        "chat_messages": {"content": answer, "chat_id": chat, "created_at": answer_at},
    }


class BuildDashboardTests(unittest.TestCase):
    def test_summary_thumbs_comments_and_question_pairing(self):
        rows = [
            feedback("f2", "down", "c1", "2026-10-02T09:05:00", reasons=["정보가 부정확해요"], comment="학점이 틀려요"),
            feedback("f1", "up", "c1", "2026-10-02T09:01:00"),
        ]
        user_messages = {"c1": [
            {"chat_id": "c1", "content": "졸업학점?", "created_at": "2026-10-02T09:00:00"},
            {"chat_id": "c1", "content": "전공필수는?", "created_at": "2026-10-02T09:04:00"},
        ]}
        result = build_dashboard(
            feedback_rows=rows, user_messages_by_chat=user_messages, recent_questions=[],
            total_feedback=4, up_count=3, total_questions=12,
            now=datetime(2026, 10, 2, tzinfo=timezone.utc),
        )
        summary = result["summary"]
        self.assertEqual((4, 3, 1, 0.75, 12), (summary["totalFeedback"], summary["upCount"],
                                               summary["downCount"], summary["satisfactionRate"],
                                               summary["totalQuestions"]))
        thumbs = result["feedback"]["thumbs"]
        # each answer is paired with the latest user question before it in the same chat
        self.assertEqual(["전공필수는?", "졸업학점?"], [t["question"] for t in thumbs])
        self.assertEqual(["down", "up"], [t["kind"] for t in thumbs])
        self.assertEqual(["정보가 부정확해요"], thumbs[0]["reasons"])
        self.assertEqual([{"id": "f2", "comment": "학점이 틀려요", "relatedQuestion": "전공필수는?",
                           "createdAt": rows[0]["created_at"]}], result["feedback"]["comments"])

    def test_no_feedback_has_no_satisfaction_rate(self):
        result = build_dashboard(feedback_rows=[], user_messages_by_chat={}, recent_questions=[],
                                 total_feedback=0, up_count=0, total_questions=0)
        self.assertIsNone(result["summary"]["satisfactionRate"])
        self.assertEqual([], result["feedback"]["thumbs"])

    def test_missing_question_is_labelled(self):
        result = build_dashboard(feedback_rows=[feedback("f1", "up", "c9", "2026-10-02T09:00:00")],
                                 user_messages_by_chat={}, recent_questions=[],
                                 total_feedback=1, up_count=1, total_questions=0)
        self.assertEqual("(질문을 찾을 수 없음)", result["feedback"]["thumbs"][0]["question"])

    def test_top_questions_group_trivial_variants(self):
        questions = ["졸업학점 알려줘", "졸업학점  알려줘?", "졸업학점 알려줘!", "휴학 신청", "", "  "]
        top = build_dashboard(feedback_rows=[], user_messages_by_chat={}, recent_questions=questions,
                              total_feedback=0, up_count=0, total_questions=6)
        self.assertEqual([("졸업학점 알려줘", 3), ("휴학 신청", 1)],
                         [(q["question"], q["count"]) for q in top["topQuestions"]])
        self.assertEqual(4, top["summary"]["questionSampleSize"])
        self.assertEqual(normalize_question("A  b?!"), "a b")


class RequireAdminTests(unittest.TestCase):
    def client(self, admin_emails, caller_email):
        app = FastAPI()

        @app.get("/secret")
        def secret(user_id: str = Depends(require_admin)):
            return {"user": user_id}

        def fake_user(request: Request) -> str:
            request.state.user_email = caller_email
            return "user-1"

        app.dependency_overrides[require_user] = fake_user
        app.dependency_overrides[get_state] = lambda: SimpleNamespace(
            settings=SimpleNamespace(admin_emails=admin_emails))
        return TestClient(app)

    def test_allowlisted_admin_passes_case_insensitively(self):
        response = self.client(["Admin@PKNU.ac.kr"], "admin@pknu.ac.kr").get("/secret")
        self.assertEqual(200, response.status_code)

    def test_other_user_is_forbidden(self):
        response = self.client(["admin@pknu.ac.kr"], "student@pknu.ac.kr").get("/secret")
        self.assertEqual(403, response.status_code)

    def test_empty_allowlist_denies_everyone(self):
        response = self.client([], "admin@pknu.ac.kr").get("/secret")
        self.assertEqual(403, response.status_code)


class AdminMeTests(unittest.TestCase):
    def client(self, admin_emails, caller_email):
        app = FastAPI()
        app.include_router(admin_router)

        def fake_user(request: Request) -> str:
            request.state.user_email = caller_email
            return "user-1"

        app.dependency_overrides[require_user] = fake_user
        app.dependency_overrides[get_state] = lambda: SimpleNamespace(
            settings=SimpleNamespace(admin_emails=admin_emails))
        return TestClient(app)

    def test_reports_admin_and_non_admin(self):
        self.assertEqual({"isAdmin": True},
                         self.client(["Admin@PKNU.ac.kr"], "admin@pknu.ac.kr").get("/admin/me").json())
        self.assertEqual({"isAdmin": False},
                         self.client(["admin@pknu.ac.kr"], "student@pknu.ac.kr").get("/admin/me").json())

    def test_requires_login(self):
        app = FastAPI()
        app.include_router(admin_router)
        app.dependency_overrides[get_state] = lambda: SimpleNamespace(
            settings=SimpleNamespace(admin_emails=["admin@pknu.ac.kr"]))
        self.assertEqual(401, TestClient(app).get("/admin/me").status_code)


if __name__ == "__main__":
    unittest.main()

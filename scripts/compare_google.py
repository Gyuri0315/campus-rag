"""Compare campus-rag answers against Google AI Overview on the same questions.

Two steps:

1. ``run``    — ask our backend every question in eval/google_compare/cases.jsonl
                (real app in-process via TestClient, login bypassed), LLM-grade
                the answers, and write a CSV template for Google's answers.
2. ``report`` — after the Google answers are pasted into the CSV, grade them
                with the same judge and write a side-by-side markdown report.

Usage:
    python scripts/compare_google.py run
    python scripts/compare_google.py report
    python scripts/compare_google.py template   # rebuild the Google CSV (refused once any answer is filled)

Cases with ``answer_status: "unverified"`` have no answer key yet; they are
reported as "미확정" and left out of the per-type denominators.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from eval.tools import grade_eval_results as grader  # noqa: E402  (loads backend/.env)
from eval.tools.eval_ask import _read_jsonl, _result_row  # noqa: E402

CASES_PATH = PROJECT_ROOT / "eval" / "google_compare" / "cases.jsonl"
GOOGLE_CSV = PROJECT_ROOT / "eval" / "google_compare" / "google_answers.csv"
RESULTS_DIR = PROJECT_ROOT / "eval" / "results"
OURS_RESULTS = RESULTS_DIR / "google_compare_ours.jsonl"
GOOGLE_RESULTS = RESULTS_DIR / "google_compare_google.jsonl"
REPORT_PATH = RESULTS_DIR / "google_compare_report.md"

TYPE_LABELS = {
    "recent": "최근 공지(2주 이내)",
    "attachment": "첨부파일 속 내용",
    "semester": "이번 학기 특정",
    "dept": "학과 고유 정보",
    "cohort": "학과·입학년도별 조건",
    "unanswerable": "자료에 없는 질문",
    "general": "일반 학사(대조군)",
}
NO_INFO_MARKERS = (
    "찾을 수 없", "확인되지 않", "확인할 수 없", "정보가 없", "자료가 없", "제공되지 않",
    "알 수 없", "포함되어 있지 않", "명시되어 있지 않", "나와 있지 않",
)


def ask_ours(cases: list[dict[str, Any]], output: Path) -> None:
    from fastapi.testclient import TestClient

    from app.main import create_app
    from app.rate_limit import enforce_ask_rate_limit

    app = create_app()
    app.dependency_overrides[enforce_ask_rate_limit] = lambda: "google-compare"
    rows = []
    with TestClient(app) as client:
        for i, case in enumerate(cases, start=1):
            started = time.perf_counter()
            response = client.post("/ask", json={"question": case["question"]})
            elapsed = time.perf_counter() - started
            try:
                body = response.json()
            except ValueError:
                body = {"error": response.text[:500]}
            if response.status_code != 200:
                body = {"error": body}
            row = _result_row(
                question_row=case, status_code=response.status_code,
                response=body, elapsed_seconds=elapsed,
            )
            row["compare_type"] = case.get("compare_type")
            rows.append(row)
            print(f"[{i}/{len(cases)}] {case['id']} {response.status_code} {elapsed:.1f}s", flush=True)
    _write_jsonl(output, rows)


# Columns of the hand-filled Google sheet. Rows are sorted by fill priority
# (1 = deadline this week, the Google result changes once it passes).
CSV_ID, CSV_ANSWER = "질문ID", "구글 답변"
CSV_COLUMNS = [
    "우선순위", CSV_ID, "질문", "유형", "날짜 민감도", "날짜 메모",
    "구글 기능(AI 개요/AI 모드)", CSV_ANSWER, "답변 날짜", "출처 URL 유무(있음/없음)", "메모",
]


def write_google_template(cases: list[dict[str, Any]], *, force: bool = False) -> None:
    if GOOGLE_CSV.exists() and not force:
        print(f"keep existing {GOOGLE_CSV.relative_to(PROJECT_ROOT)} (not overwritten)")
        return
    if GOOGLE_CSV.exists() and any(row.get(CSV_ANSWER, "").strip() for row in read_google_answers().values()):
        raise SystemExit(f"{GOOGLE_CSV.relative_to(PROJECT_ROOT)} already has Google answers; not overwritten")
    ordered = sorted(cases, key=lambda case: (case.get("fill_priority", 9), case["id"]))
    # utf-8-sig so Excel opens Korean text correctly.
    with GOOGLE_CSV.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(CSV_COLUMNS)
        for case in ordered:
            writer.writerow([
                case.get("fill_priority", ""), case["id"], case["question"],
                TYPE_LABELS.get(case.get("compare_type", ""), case.get("compare_type", "")),
                case.get("date_sensitivity", ""), case.get("date_note", ""), "", "", "", "", "",
            ])
    print(f"wrote {GOOGLE_CSV.relative_to(PROJECT_ROOT)}")


def read_google_answers() -> dict[str, dict[str, str]]:
    with GOOGLE_CSV.open(encoding="utf-8-sig", newline="") as f:
        return {row[CSV_ID]: row for row in csv.DictReader(f) if row.get(CSV_ID)}


def build_google_results(cases: list[dict[str, Any]], output: Path) -> int:
    answers = read_google_answers()
    rows = []
    filled = 0
    for case in cases:
        answer = (answers.get(case["id"], {}).get(CSV_ANSWER) or "").strip()
        filled += bool(answer)
        row = _result_row(question_row=case, status_code=None, response={"answer": answer}, elapsed_seconds=0.0)
        row["compare_type"] = case.get("compare_type")
        rows.append(row)
    _write_jsonl(output, rows)
    return filled


def grade(results: Path) -> Path:
    graded_path = results.with_suffix(".graded.jsonl")
    model = grader.os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
    grader.run(results, graded_path, model, timeout=30.0, limit=None, sleep_seconds=0.2)
    return graded_path


def verdict(row: dict[str, Any], case: dict[str, Any]) -> tuple[str, float | None]:
    """Collapse the judge output of one case into a single label + fact score."""
    answer = (row.get("answer") or "").strip()
    if not answer:
        return "답변 없음", None
    if case.get("answer_status") == "unverified":
        # No confirmed answer key yet (see the case's evidence_note).
        return "미확정", None
    judged = row.get("judged") or {}
    violated = any(item.get("verdict") == "violated" for item in judged.get("forbidden") or [])
    if case.get("answerable") is False:
        return ("지어냄" if violated else "정직(모름)"), None
    facts = judged.get("facts") or []
    if not facts:
        return "채점 실패", None
    score = sum(1.0 if f.get("verdict") == "covered" else 0.5 if f.get("verdict") == "partial" else 0.0
                for f in facts) / len(facts)
    if violated:
        return "틀림", score
    if score >= 0.75:
        return "정확", score
    if score >= 0.3:
        return "부분", score
    if any(marker in answer for marker in NO_INFO_MARKERS):
        return "모름", score
    return "실패", score


GOOD = {"정확", "정직(모름)"}


def write_report(cases: list[dict[str, Any]], ours: Path, google: Path) -> None:
    ours_rows = {r["id"]: r for r in _read_graded(ours)}
    google_rows = {r["id"]: r for r in _read_graded(google)}
    by_type: dict[str, list[tuple[str, str]]] = defaultdict(list)
    lines_detail = []
    for case in cases:
        o_label, o_score = verdict(ours_rows.get(case["id"], {}), case)
        g_label, g_score = verdict(google_rows.get(case["id"], {}), case)
        by_type[case["compare_type"]].append((o_label, g_label))
        lines_detail.append(
            f"| {case['id']} | {TYPE_LABELS[case['compare_type']]} | {case['question']} "
            f"| {o_label}{_fmt(o_score)} | {g_label}{_fmt(g_score)} |"
        )

    out = [
        f"# 구글 AI vs campus-rag 비교 ({date.today().isoformat()})",
        "",
        "같은 질문을 양쪽에 던지고, 같은 채점기(LLM judge)·같은 정답 체크리스트로 채점했다.",
        "- **정확**: 필수 사실 75% 이상 · **부분**: 30~75% · **틀림**: 잘못된 정보를 단정 · "
        "**모름**: 모른다고 답함 · **정직(모름)**: 자료에 없는 질문에 모른다고 답함(정답) · **지어냄**: 없는 정보를 만들어냄",
        "",
        "## 유형별 요약 (정답 개수 / 질문 수)",
        "",
        "정답 미확정 문항(answer_status=unverified)은 분모에서 빼고 따로 센다.",
        "",
        "| 유형 | campus-rag | 구글 AI | 구글 '틀림·지어냄' | 미확정 |",
        "|---|---|---|---|---|",
    ]
    for ctype, label in TYPE_LABELS.items():
        pairs = by_type.get(ctype, [])
        if not pairs:
            continue
        scored = [(o, g) for o, g in pairs if o != "미확정"]
        ours_ok = sum(o in GOOD for o, _ in scored)
        google_ok = sum(g in GOOD for _, g in scored)
        google_wrong = sum(g in {"틀림", "지어냄"} for _, g in scored)
        out.append(
            f"| {label} | {ours_ok}/{len(scored)} | {google_ok}/{len(scored)} | {google_wrong} "
            f"| {len(pairs) - len(scored)} |"
        )
    out += ["", "## 질문별 결과", "", "| id | 유형 | 질문 | campus-rag | 구글 AI |", "|---|---|---|---|---|"]
    out += lines_detail
    REPORT_PATH.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"wrote {REPORT_PATH.relative_to(PROJECT_ROOT)}")


def _fmt(score: float | None) -> str:
    return "" if score is None else f" ({score:.0%})"


def _read_graded(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["run", "report", "template"])
    args = parser.parse_args()
    cases = _read_jsonl(CASES_PATH)

    if args.command == "template":
        # Rebuild the Google sheet (refuses if any answer is already filled in).
        write_google_template(cases, force=True)
        return 0

    if args.command == "run":
        ask_ours(cases, OURS_RESULTS)
        grade(OURS_RESULTS)
        write_google_template(cases)
        return 0

    filled = build_google_results(cases, GOOGLE_RESULTS)
    print(f"google answers filled: {filled}/{len(cases)}")
    grade(GOOGLE_RESULTS)
    write_report(cases, OURS_RESULTS.with_suffix(".graded.jsonl"), GOOGLE_RESULTS.with_suffix(".graded.jsonl"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""LLM-judge qualitative grading for eval_ask.py result files.

Checks each answerable case's generated answer against its structured
`required_facts` (covered/partial/missing) and `forbidden_claims`
(violated/ok) using an LLM judge, since keyword matching alone can't tell
whether an answer actually conveys a fact when `keywords` is empty (common
in this dataset) or phrases it differently than the label.

Usage:
    python scripts/grade_eval_results.py --results eval/results/regression_category2_fix_20260915.jsonl
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / "backend" / ".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

JUDGE_SYSTEM_PROMPT = (
    "당신은 대학 RAG 챗봇의 답변 품질을 채점하는 엄격하지만 공정한 평가자입니다. "
    "질문, 실제로 생성된 답변, 정답 체크리스트(required_facts), 금지된 주장(forbidden_claims)이 주어집니다.\n\n"
    "각 required_fact에 대해 답변이 그 사실을 얼마나 반영했는지 판정하세요:\n"
    "- covered: 사실이 정확하게 답변에 포함됨\n"
    "- partial: 관련은 있지만 불완전하거나 모호하게 언급됨\n"
    "- missing: 답변에 전혀 언급되지 않음\n\n"
    "각 forbidden_claim에 대해 답변이 실제로 그 주장을 '사실인 것처럼 확정 제시'하고 있는지 판정하세요. "
    "세 가지 중 하나로만 답하세요:\n"
    "- violated: 답변이 금지된 주장을 별다른 단서 없이 확정된 사실처럼 제시함\n"
    "- hedged: 답변이 그 주장과 관련된 구체적 정보(예: 특정 학기의 날짜·금액)를 언급하긴 하지만, "
    "그것이 특정 시점의 예시일 뿐이며 최신 확인이 필요하다는 점을 명시적으로 함께 밝힘. "
    "완벽하진 않지만 사용자를 오도하지 않으려는 시도가 분명한 경우\n"
    "- ok: 답변이 그 주장을 아예 하지 않거나, 명시적으로 부인/거절함\n\n"
    "판정 시 반드시 지켜야 할 원칙:\n"
    "1. 답변이 '확인할 수 없다', '예측할 수 없다', '자료에 없다', '알려진 바 없다'처럼 명시적으로 "
    "그 사실을 모른다고 인정하거나 요청을 거절하는 경우, forbidden_claim의 주제를 답변 문장 안에서 "
    "언급했다는 이유만으로 violated로 판정하지 마세요. 실제로 그 주장을 '사실이다'라고 단정했을 때만 "
    "violated입니다. 정직한 거절/모름 인정은 항상 ok입니다.\n"
    "2. 답변이 특정 연도·학기의 과거 값(날짜, 금액 등)을 예시로 들면서 '학기마다 다르다', '최신 공지를 "
    "확인해야 한다', '변동될 수 있다' 등 명확한 단서를 붙였다면, 그 부분은 violated가 아니라 hedged로 "
    "판정하세요. 단서 없이 값만 단독으로 확정 제시한 경우에만 violated입니다.\n"
    "3. 표현이 다르더라도 같은 의미를 전달하면 covered/violated로 판정하세요.\n\n"
    "반드시 아래 JSON 스키마로만 답하세요. 다른 텍스트를 추가하지 마세요:\n"
    '{"facts": [{"id": "<fact id>", "verdict": "covered|partial|missing", "reason": "<한 문장 근거>"}], '
    '"forbidden": [{"index": <0-based int>, "verdict": "violated|hedged|ok", "reason": "<한 문장 근거>"}]}'
)


def build_user_prompt(case: dict[str, Any]) -> str:
    facts = case.get("required_facts") or []
    forbidden = case.get("forbidden_claims") or []
    facts_block = "\n".join(
        f"{i + 1}. [{fact['id']}] {fact['description']}"
        + (f" (keywords: {', '.join(fact['keywords'])})" if fact.get("keywords") else "")
        for i, fact in enumerate(facts)
    ) or "(없음)"
    forbidden_block = "\n".join(f"{i}. {claim}" for i, claim in enumerate(forbidden)) or "(없음)"
    return (
        f"질문: {case.get('question', '')}\n\n"
        f"생성된 답변:\n{case.get('answer', '')}\n\n"
        f"필수 사실 체크리스트:\n{facts_block}\n\n"
        f"금지된 주장:\n{forbidden_block}"
    )


def grade_case(client: OpenAI, model: str, case: dict[str, Any], timeout: float) -> dict[str, Any]:
    facts = case.get("required_facts") or []
    forbidden = case.get("forbidden_claims") or []
    if not facts and not forbidden:
        return {"facts": [], "forbidden": []}

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
            {"role": "user", "content": build_user_prompt(case)},
        ],
        temperature=0.0,
        max_tokens=1200,
        timeout=timeout,
        response_format={"type": "json_object"},
    )
    raw = (response.choices[0].message.content or "{}").strip()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        log.error("judge returned invalid JSON for id=%s: %r", case.get("id"), raw[:200])
        return {"facts": [], "forbidden": [], "judge_error": "invalid_json"}
    parsed.setdefault("facts", [])
    parsed.setdefault("forbidden", [])
    return parsed


def summarize(graded: list[dict[str, Any]]) -> dict[str, Any]:
    total_facts = 0
    covered = partial = missing = 0
    total_forbidden = 0
    violated = 0
    hedged = 0
    cases_with_violation: list[str] = []
    cases_with_hedge: list[str] = []
    cases_fully_covered: list[str] = []
    cases_with_missing: list[str] = []
    graded_case_count = 0

    for row in graded:
        judged = row.get("judged") or {}
        facts_judged = judged.get("facts") or []
        forbidden_judged = judged.get("forbidden") or []
        if not facts_judged and not forbidden_judged and not row.get("required_facts") and not row.get("forbidden_claims"):
            continue
        graded_case_count += 1

        case_missing = False
        for fact in facts_judged:
            total_facts += 1
            verdict = fact.get("verdict")
            if verdict == "covered":
                covered += 1
            elif verdict == "partial":
                partial += 1
            else:
                missing += 1
                case_missing = True
        if case_missing:
            cases_with_missing.append(row["id"])
        elif facts_judged:
            cases_fully_covered.append(row["id"])

        case_violated = False
        case_hedged = False
        for item in forbidden_judged:
            total_forbidden += 1
            verdict = item.get("verdict")
            if verdict == "violated":
                violated += 1
                case_violated = True
            elif verdict == "hedged":
                hedged += 1
                case_hedged = True
        if case_violated:
            cases_with_violation.append(row["id"])
        elif case_hedged:
            cases_with_hedge.append(row["id"])

    fact_coverage = (covered + 0.5 * partial) / total_facts if total_facts else None
    return {
        "graded_case_count": graded_case_count,
        "total_facts": total_facts,
        "facts_covered": covered,
        "facts_partial": partial,
        "facts_missing": missing,
        "fact_coverage_score": round(fact_coverage, 4) if fact_coverage is not None else None,
        "total_forbidden_claims": total_forbidden,
        "forbidden_claims_violated": violated,
        "forbidden_claims_hedged": hedged,
        "cases_with_any_missing_fact": cases_with_missing,
        "cases_fully_covered": cases_fully_covered,
        "cases_with_forbidden_violation": cases_with_violation,
        "cases_with_hedge_only": cases_with_hedge,
    }


def run(results_path: Path, output_path: Path, model: str, timeout: float, limit: int | None, sleep_seconds: float) -> None:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise SystemExit("OPENAI_API_KEY not set (checked backend/.env)")
    client = OpenAI(api_key=api_key)

    cases: list[dict[str, Any]] = []
    with results_path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                cases.append(json.loads(line))
    if limit:
        cases = cases[:limit]

    graded: list[dict[str, Any]] = []
    for i, case in enumerate(cases, start=1):
        case_id = case.get("id", f"row_{i}")
        if case.get("error") or not (case.get("answer") or "").strip():
            log.info("[%d/%d] %s SKIP (no answer / error)", i, len(cases), case_id)
            graded.append({**case, "judged": {"facts": [], "forbidden": []}, "skipped": "no_answer"})
            continue
        try:
            judged = grade_case(client, model, case, timeout)
        except Exception:
            log.exception("[%d/%d] %s grading failed", i, len(cases), case_id)
            judged = {"facts": [], "forbidden": [], "judge_error": "exception"}
        graded.append({**case, "judged": judged})
        n_facts = len(judged.get("facts") or [])
        n_forbidden = len(judged.get("forbidden") or [])
        log.info("[%d/%d] %s OK facts=%d forbidden=%d", i, len(cases), case_id, n_facts, n_forbidden)
        if sleep_seconds:
            time.sleep(sleep_seconds)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        for row in graded:
            slim = {
                "id": row["id"],
                "question": row.get("question"),
                "answer": row.get("answer"),
                "required_facts": row.get("required_facts"),
                "forbidden_claims": row.get("forbidden_claims"),
                "judged": row.get("judged"),
                "skipped": row.get("skipped"),
            }
            f.write(json.dumps(slim, ensure_ascii=False) + "\n")

    summary = summarize(graded)
    summary_path = output_path.with_suffix(".summary.json")
    with summary_path.open("w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    log.info("=" * 60)
    log.info(
        "graded_cases=%d fact_coverage=%s (covered=%d partial=%d missing=%d / total=%d) "
        "forbidden_violations=%d hedged=%d ok=%d (total=%d)",
        summary["graded_case_count"],
        summary["fact_coverage_score"],
        summary["facts_covered"],
        summary["facts_partial"],
        summary["facts_missing"],
        summary["total_facts"],
        summary["forbidden_claims_violated"],
        summary["forbidden_claims_hedged"],
        summary["total_forbidden_claims"] - summary["forbidden_claims_violated"] - summary["forbidden_claims_hedged"],
        summary["total_forbidden_claims"],
    )
    log.info("saved details=%s summary=%s", output_path, summary_path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="LLM-judge grading for eval_ask.py results.")
    parser.add_argument("--results", type=Path, required=True, help="eval_ask.py output JSONL to grade.")
    parser.add_argument("--output", type=Path, default=None, help="Output JSONL path (default: <results>.graded.jsonl).")
    parser.add_argument("--model", default=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"))
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--sleep", type=float, default=0.2)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output = args.output or args.results.with_suffix(".graded.jsonl")
    run(args.results, output, args.model, args.timeout, args.limit, args.sleep)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

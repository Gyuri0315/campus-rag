"""Build the balanced 100-question draft evaluation bank."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = PROJECT_ROOT / "eval" / "drafts" / "question_bank_100.jsonl"

# 최초 39개 regression 목록과 정확히 일치했던 질문. 현재 regression은 라벨
# 템플릿이므로 이를 런타임에 다시 읽어 origin을 계산하면 결과가 달라진다.
ORIGINAL_QUESTION_MATCHES = {
    "졸업하려면 총 몇 학점 이상을 이수해야 하나요?",
    "복수전공을 신청할 수 있는 자격 조건이 궁금해요.",
    "교직과정 이수 신청은 어떤 학생이 할 수 있나요?",
    "현장실습학기제 참여 조건과 학점 인정 기준을 알려줘.",
    "출석 인정이나 결석 처리 기준은 어떻게 되나요?",
    "수강신청 정정 기간은 언제인가요?",
    "컴퓨터·인공지능공학부 사무실 연락처가 어떻게 되나요?",
    "인공지능전공 문의 전화번호를 알려줘.",
    "대연캠퍼스에서 용당캠퍼스로 가는 셔틀버스 이용 방법을 알려줘.",
    "도서관 이용 시간과 모바일 도서관 이용 방법이 궁금해요.",
    "교내 식당 위치와 운영 시간이 궁금해요.",
    "HelloLMS는 어디에서 접속하고 무엇에 사용하나요?",
    "2022년 대만 장학금 신청 서류에는 무엇이 포함되나요?",
    "2027학년도 의과대학 입학 전형 일정 알려줘.",
    "부경대 근처 원룸 월세 평균은 얼마인가요?",
    "부경대 학생에게 가장 인기 있는 맛집을 추천해줘.",
    "내 학점이 3.2인데 대기업 취업 가능성이 높을까?",
    "오늘 대연캠퍼스 날씨에 맞춰 우산이 필요할까?",
}

TARGET_COUNTS = {
    "규정·이수 조건": 20,
    "신청 기간·일정": 15,
    "신청 방법·절차": 15,
    "연락처·위치": 10,
    "학생생활 정보": 10,
    "특정 공지·첨부파일": 10,
    "학과·학년도 구분": 5,
    "부분 답변 가능": 5,
    "데이터에 없는 질문": 10,
}

# (question, scenario_tags, difficulty)
QUESTION_GROUPS: dict[str, list[tuple[str, list[str], str]]] = {
    "규정·이수 조건": [
        ("졸업하려면 총 몇 학점 이상을 이수해야 하나요?", ["ambiguous_scope", "exact_value"], "hard"),
        ("복수전공을 신청할 수 있는 자격 조건이 궁금해요.", ["full_answer"], "medium"),
        ("부전공 신청 자격과 이수학점 기준을 알려줘.", ["full_answer", "exact_value"], "medium"),
        ("교직과정 이수 신청은 어떤 학생이 할 수 있나요?", ["full_answer"], "medium"),
        ("조기졸업을 신청하려면 어떤 조건을 충족해야 하나요?", ["full_answer", "exact_value"], "medium"),
        ("현장실습학기제 참여 조건과 학점 인정 기준을 알려줘.", ["full_answer", "exact_value"], "hard"),
        ("출석 인정이나 결석 처리 기준은 어떻게 되나요?", ["full_answer"], "medium"),
        ("재수강은 어떤 성적을 받은 과목부터 가능한가요?", ["full_answer", "exact_value"], "medium"),
        ("재이수 과목의 성적 표기와 취득 가능 성적 상한을 알려줘.", ["full_answer", "exact_value"], "hard"),
        ("전과 신청 자격과 지원 제한 사항이 궁금해요.", ["full_answer"], "medium"),
        ("마이크로전공 이수 요건과 수료 기준을 알려줘.", ["full_answer", "exact_value"], "medium"),
        ("학사경고 기준과 학사경고를 받은 뒤의 조치는 무엇인가요?", ["full_answer", "exact_value"], "hard"),
        ("복수전공과 부전공을 동시에 이수할 수 있나요?", ["full_answer"], "medium"),
        ("전공필수 과목을 이수하지 않으면 졸업할 수 있나요?", ["full_answer"], "medium"),
        ("최대 수강신청 가능 학점은 몇 학점인가요?", ["ambiguous_scope", "exact_value"], "hard"),
        ("학점포기 제도가 현재도 운영되는지 알려줘.", ["temporal_conflict"], "hard"),
        ("휴학할 수 있는 최대 학기 수는 얼마인가요?", ["exact_value"], "medium"),
        ("일반휴학과 군휴학의 기간 제한이 어떻게 다른가요?", ["full_answer"], "hard"),
        ("졸업논문이나 졸업작품 제출이 졸업 필수 조건인가요?", ["ambiguous_scope"], "hard"),
        ("타 학과 전공 과목을 전공학점으로 인정받을 수 있는 조건은 무엇인가요?", ["full_answer"], "hard"),
    ],
    "신청 기간·일정": [
        ("졸업유예는 언제 신청하나요?", ["temporal_conflict", "exact_value"], "hard"),
        ("휴학 신청 기간은 언제인가요?", ["temporal_conflict", "exact_value"], "medium"),
        ("복학 신청 기간을 알려줘.", ["temporal_conflict", "exact_value"], "medium"),
        ("수강신청 정정 기간은 언제인가요?", ["temporal_conflict", "exact_value"], "medium"),
        ("계절수업 신청 기간과 등록금 납부 기간을 알려줘.", ["temporal_conflict", "exact_value"], "hard"),
        ("국가장학금 신청 기간은 언제인가요?", ["temporal_conflict"], "medium"),
        ("근로장학금 신청은 언제 시작하나요?", ["temporal_conflict"], "medium"),
        ("학생생활관 신입생 모집 기간과 합격자 발표일을 알려줘.", ["temporal_conflict", "exact_value"], "hard"),
        ("성적 열람 및 이의신청 기간은 언제인가요?", ["temporal_conflict", "exact_value"], "medium"),
        ("등록금 납부 기간과 분할납부 신청 기간을 알려줘.", ["temporal_conflict", "exact_value"], "hard"),
        ("복수전공 신청 기간은 언제인가요?", ["temporal_conflict"], "medium"),
        ("전과 신청 일정과 합격자 발표일을 알려줘.", ["temporal_conflict", "exact_value"], "hard"),
        ("교환학생 모집 공고는 보통 언제 올라오나요?", ["relevant_but_unanswerable"], "hard"),
        ("졸업논문 제출 기간은 언제인가요?", ["ambiguous_scope", "temporal_conflict"], "hard"),
        ("캡스톤디자인 과제 신청과 결과보고서 제출 일정이 궁금해요.", ["temporal_conflict", "exact_value"], "hard"),
    ],
    "신청 방법·절차": [
        ("캡스톤디자인 신청은 어떻게 하나요?", ["full_answer"], "medium"),
        ("휴학은 어디에서 어떤 순서로 신청하나요?", ["full_answer"], "medium"),
        ("복학 신청 절차와 승인 확인 방법을 알려줘.", ["full_answer"], "medium"),
        ("복수전공 신청 절차와 제출 서류를 알려줘.", ["full_answer"], "medium"),
        ("부전공은 어디에서 신청하고 결과는 어떻게 확인하나요?", ["full_answer"], "medium"),
        ("성적 이의신청은 어디에서 어떤 절차로 하나요?", ["full_answer"], "medium"),
        ("등록금 분할납부는 어떻게 신청하나요?", ["full_answer"], "medium"),
        ("국가장학금 신청 절차와 제출 서류를 알려줘.", ["full_answer"], "medium"),
        ("근로장학생은 어디에서 신청하고 선발 결과를 어떻게 확인하나요?", ["full_answer"], "medium"),
        ("학생생활관 입사 신청 절차를 알려줘.", ["full_answer"], "medium"),
        ("현장실습학기제 참여 신청부터 학점 인정까지의 절차를 알려줘.", ["full_answer"], "hard"),
        ("교환학생 지원 절차와 필요한 서류를 알려줘.", ["full_answer"], "hard"),
        ("전과 신청은 어느 시스템에서 진행하나요?", ["full_answer"], "medium"),
        ("마이크로전공 신청과 이수 완료 확인 방법을 알려줘.", ["full_answer"], "medium"),
        ("졸업유예 신청 후 등록 절차는 어떻게 되나요?", ["full_answer"], "hard"),
    ],
    "연락처·위치": [
        ("컴퓨터·인공지능공학부 사무실 연락처가 어떻게 되나요?", ["exact_value"], "easy"),
        ("인공지능전공 문의 전화번호를 알려줘.", ["exact_value", "ambiguous_scope"], "medium"),
        ("컴퓨터공학전공 사무실은 어디에 있나요?", ["exact_value"], "medium"),
        ("국제교류 프로그램은 어느 부서에 문의해야 하나요?", ["exact_value"], "medium"),
        ("보건진료소 위치와 전화번호를 알려줘.", ["exact_value"], "easy"),
        ("학생생활관 행정실 연락처와 위치를 알려줘.", ["exact_value", "ambiguous_scope"], "medium"),
        ("장학금 관련 문의는 어느 부서로 전화해야 하나요?", ["exact_value"], "medium"),
        ("학사 관련 증명서 발급 문의처를 알려줘.", ["exact_value"], "medium"),
        ("대연캠퍼스 도서관 위치와 대표 전화번호가 궁금해요.", ["exact_value"], "medium"),
        ("취업지원 부서의 위치와 연락처를 알려줘.", ["exact_value"], "medium"),
    ],
    "학생생활 정보": [
        ("대연캠퍼스에서 용당캠퍼스로 가는 셔틀버스 이용 방법을 알려줘.", ["full_answer"], "medium"),
        ("대연캠퍼스 학생생활관은 어떤 유형의 방을 신청할 수 있나요?", ["ambiguous_scope"], "medium"),
        ("보건진료소 이용 대상과 운영 시간을 알려줘.", ["exact_value"], "medium"),
        ("도서관 이용 시간과 모바일 도서관 이용 방법이 궁금해요.", ["partial_answer"], "hard"),
        ("교내 식당 위치와 운영 시간이 궁금해요.", ["temporal_conflict", "exact_value"], "medium"),
        ("HelloLMS는 어디에서 접속하고 무엇에 사용하나요?", ["full_answer"], "easy"),
        ("학교 무선인터넷을 이용하는 방법을 알려줘.", ["full_answer"], "medium"),
        ("학생증을 분실했을 때 재발급받는 방법을 알려줘.", ["full_answer"], "medium"),
        ("증명서 자동발급기는 어디에 있나요?", ["exact_value"], "medium"),
        ("교내 상담 서비스를 이용하려면 어떻게 해야 하나요?", ["full_answer"], "medium"),
    ],
    "특정 공지·첨부파일": [
        ("2025년 일본 도쿄해양대 프로그램의 신청 대상과 제출 서류를 알려줘.", ["attachment", "exact_value"], "hard"),
        ("2022년 대만 장학금 신청 서류에는 무엇이 포함되나요?", ["attachment", "temporal_conflict"], "hard"),
        ("외국인 대학원 신입생 모집 추천서는 어떻게 제출해야 하나요?", ["attachment"], "hard"),
        ("최근 캡스톤디자인 공지에 첨부된 신청서 파일명이 무엇인가요?", ["attachment", "temporal_conflict"], "hard"),
        ("현장실습학기제 참여 신청서에 반드시 작성해야 하는 항목을 알려줘.", ["attachment"], "hard"),
        ("교환학생 모집 공고의 제출 서류 체크리스트를 알려줘.", ["attachment", "temporal_conflict"], "hard"),
        ("학생생활관 모집 공고에 나온 입사 제출 서류를 알려줘.", ["attachment", "exact_value"], "hard"),
        ("장학금 공지에 첨부된 개인정보 동의서도 제출해야 하나요?", ["attachment", "relevant_but_unanswerable"], "hard"),
        ("졸업작품 제출 공지의 파일 형식과 제출 경로를 알려줘.", ["attachment", "ambiguous_scope"], "hard"),
        ("학부 공지에 첨부된 교과목 이수표에서 전공필수 과목을 알려줘.", ["attachment", "exact_value"], "hard"),
    ],
    "학과·학년도 구분": [
        ("2024학년도 입학생의 컴퓨터공학전공 졸업학점 기준을 알려줘.", ["cohort_specific", "exact_value"], "hard"),
        ("2022학년도와 2025학년도 입학생의 졸업요건이 어떻게 다른가요?", ["cohort_specific", "temporal_conflict"], "hard"),
        ("인공지능전공과 컴퓨터공학전공의 전공필수 과목이 같은가요?", ["major_specific"], "hard"),
        ("입학연도를 말하지 않으면 졸업 소요학점을 하나로 안내할 수 있나요?", ["ambiguous_scope", "cohort_specific"], "hard"),
        ("대연캠퍼스 소속 학과와 용당캠퍼스 소속 학과의 문의처를 구분해 알려줘.", ["major_specific", "exact_value"], "hard"),
    ],
    "부분 답변 가능": [
        ("휴학 신청 방법과 이번 학기 정확한 신청 마감일을 함께 알려줘.", ["partial_answer", "temporal_conflict"], "hard"),
        ("장학금 확인 경로와 내가 받을 수 있는 장학금 금액을 알려줘.", ["partial_answer", "personal_prediction"], "hard"),
        ("셔틀버스 노선과 오늘 실시간 도착 시간을 알려줘.", ["partial_answer", "realtime"], "hard"),
        ("학부 사무실 연락처와 지금 담당자가 자리에 있는지 알려줘.", ["partial_answer", "realtime", "exact_value"], "hard"),
        ("복수전공 신청 조건과 내가 합격할 가능성을 알려줘.", ["partial_answer", "personal_prediction"], "hard"),
    ],
    "데이터에 없는 질문": [
        ("2027학년도 의과대학 입학 전형 일정 알려줘.", ["no_retrieval", "future_info"], "easy"),
        ("부경대 근처 원룸 월세 평균은 얼마인가요?", ["no_retrieval", "external_info"], "easy"),
        ("부경대 학생에게 가장 인기 있는 맛집을 추천해줘.", ["no_retrieval", "recommendation"], "medium"),
        ("내 학점이 3.2인데 대기업 취업 가능성이 높을까?", ["relevant_but_unanswerable", "personal_prediction"], "hard"),
        ("오늘 대연캠퍼스 날씨에 맞춰 우산이 필요할까?", ["no_retrieval", "realtime"], "easy"),
        ("다음 주 대연캠퍼스 주변 교통 체증을 예측해줘.", ["no_retrieval", "future_info"], "easy"),
        ("부경대 학생들의 평균 생활비가 얼마인지 알려줘.", ["no_retrieval", "external_info"], "medium"),
        ("내 성적으로 어느 회사에 합격할 수 있는지 순위를 매겨줘.", ["relevant_but_unanswerable", "personal_prediction"], "hard"),
        ("학교 공식 자료를 바탕으로 비트코인 가격을 전망해줘.", ["no_retrieval", "external_info"], "easy"),
        ("지금 대연캠퍼스 주차장에 빈자리가 있는지 알려줘.", ["no_retrieval", "realtime"], "easy"),
    ],
}


def build_rows() -> list[dict]:
    rows: list[dict] = []
    for case_type, questions in QUESTION_GROUPS.items():
        prefix = {
            "규정·이수 조건": "rules",
            "신청 기간·일정": "schedule",
            "신청 방법·절차": "procedure",
            "연락처·위치": "contact",
            "학생생활 정보": "student_life",
            "특정 공지·첨부파일": "notice_attachment",
            "학과·학년도 구분": "cohort_major",
            "부분 답변 가능": "partial",
            "데이터에 없는 질문": "unanswerable",
        }[case_type]
        for index, (question, scenario_tags, difficulty) in enumerate(questions, start=1):
            rows.append(
                {
                    "id": f"draft_{prefix}_{index:03d}",
                    "question": question,
                    "case_type": case_type,
                    "scenario_tags": scenario_tags,
                    "difficulty": difficulty,
                    "label_status": "needs_review",
                    "origin": "existing" if question in ORIGINAL_QUESTION_MATCHES else "new",
                }
            )
    return rows


def validate_rows(rows: list[dict]) -> None:
    counts = Counter(row["case_type"] for row in rows)
    if counts != Counter(TARGET_COUNTS):
        raise ValueError(f"distribution mismatch: expected={TARGET_COUNTS}, actual={dict(counts)}")
    questions = [row["question"] for row in rows]
    if len(questions) != len(set(questions)):
        duplicates = sorted({q for q in questions if questions.count(q) > 1})
        raise ValueError(f"duplicate questions: {duplicates}")
    required_scenarios = {
        "full_answer",
        "partial_answer",
        "relevant_but_unanswerable",
        "no_retrieval",
        "temporal_conflict",
        "ambiguous_scope",
        "exact_value",
    }
    actual_scenarios = {tag for row in rows for tag in row["scenario_tags"]}
    missing = required_scenarios - actual_scenarios
    if missing:
        raise ValueError(f"missing required scenarios: {sorted(missing)}")


def main() -> int:
    rows = build_rows()
    validate_rows(rows)
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
        newline="\n",
    )
    counts = Counter(row["case_type"] for row in rows)
    print(f"saved={OUTPUT_PATH} total={len(rows)}")
    for case_type, target in TARGET_COUNTS.items():
        print(f"{case_type}: {counts[case_type]}/{target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

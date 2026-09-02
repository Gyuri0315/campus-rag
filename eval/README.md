# Evaluation datasets

평가 케이스는 목적에 따라 분리합니다.

- `cases/smoke.jsonl`: 빠른 확인용 핵심 질문 8~15개. 작은 변경 후 실행합니다.
- `cases/regression.jsonl`: 100개 질문의 라벨 입력 서식입니다. 모든 행이 `needs_review`로 시작하며 `ready`가 되기 전에는 정확도 평가에 사용하지 않습니다.
- `cases/challenge.jsonl`: 범위 밖, 애매한 표현, 오타, 복합 조건처럼 실패 가능성이 높은 질문만 집중 평가합니다. 회귀 세트와 일부 중복될 수 있습니다.
- `schemas/eval_case.schema.json`: 구조화 평가 케이스의 JSON Schema입니다.
- `baselines/`: 코드와 모델 및 RAG 설정별 기준 점수를 보관합니다.
- `results/`: 로컬 평가 실행 결과를 저장합니다. 생성 결과는 Git에 커밋하지 않습니다.
- `drafts/question_bank_100.jsonl`: 유형 균형을 맞춘 100개 질문 초안입니다. 정답 출처와 사실 라벨을 검수하기 전에는 실행용 회귀 세트로 사용하지 않습니다.

## 실행 예시

```powershell
# 빠른 확인
.\.venv\Scripts\python.exe scripts\eval_ask.py --questions eval\cases\smoke.jsonl --output eval\results\smoke.jsonl

# 빠른 smoke 평가 (기본값)
.\.venv\Scripts\python.exe scripts\eval_ask.py

# 실패 경계 집중 평가
.\.venv\Scripts\python.exe scripts\eval_ask.py --questions eval\cases\challenge.jsonl --output eval\results\challenge.jsonl
```

`regression.jsonl`의 빈칸을 채우는 동안 기본 평가 입력은 `smoke.jsonl`입니다.
회귀 세트는 100개 행이 모두 `ready` 상태가 된 뒤 전체 평가에 사용합니다.

## 100개 질문 초안

질문 초안은 `draft_question.schema.json` 형식이며, `case_type`,
`scenario_tags`, `difficulty`, `label_status`, `origin`을 포함합니다.
`origin=existing`은 기존 regression 질문과 정확히 일치하는 질문이고,
`origin=new`는 유형 균형을 위해 추가한 질문입니다.

```powershell
# 원본 정의로 질문 뱅크 재생성
.\.venv\Scripts\python.exe scripts\build_eval_question_bank.py

# 수량, 유형 분포, 중복, 필수 시나리오 검사
.\.venv\Scripts\python.exe scripts\audit_eval_question_bank.py
```

라벨 검수 시 각 행을 `eval_case.schema.json` 형식으로 확장한 뒤
`label_status`를 `needs_review → source_verified → label_verified → ready`
순서로 변경합니다. `needs_review`인 행은 정확도 평가의 정답 데이터로 간주하지 않습니다.

## Regression 라벨 입력

각 행에서 다음 필드를 채웁니다.

- `answerable`: 답변 가능 여부
- `expected_source`: 예상 데이터셋, 제목 키워드, URL 또는 source ID
- `required_facts`: 반드시 포함되어야 하는 사실
- `forbidden_claims`: 생성하면 안 되는 주장
- `expected_no_info`: `answerable`의 반대 값
- `tags`: 검색과 답변 특성
- `expected_behavior`: 사람이 확인할 정답 행동
- `review_notes`: 출처 확인 기록

답변 불가 질문은 `expected_source`를 `null`로 설정합니다. 답변 가능 질문은
출처 조건 중 하나 이상을 반드시 채워야 합니다.

```powershell
# 현재 라벨 검수 진행률과 ready 행의 유효성 확인
.\.venv\Scripts\python.exe scripts\audit_regression_labels.py

# 질문 뱅크로부터 빈 서식을 다시 만들기(기존 입력 내용이 사라짐)
.\.venv\Scripts\python.exe scripts\build_regression_template.py
```

두 번째 명령은 `regression.jsonl`을 초기화하므로 최초 생성 또는 명시적인
재초기화가 필요할 때만 사용합니다.

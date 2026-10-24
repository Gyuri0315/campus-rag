# Evaluation datasets

Evaluation commands are in `eval/tools/` and run from the repository root with
`python -m eval.tools.<command>`.

평가 케이스는 목적에 따라 분리합니다.

- `cases/smoke.jsonl`: 빠른 확인용 핵심 질문 8~15개. 작은 변경 후 실행합니다.
- `cases/regression.jsonl`: 출처와 정답 라벨 검수를 마친 100개 질문의 회귀 평가 세트입니다. 모든 행이 `ready` 상태입니다.
- `cases/challenge.jsonl`: 범위 밖, 애매한 표현, 오타, 복합 조건처럼 실패 가능성이 높은 질문만 집중 평가합니다. 회귀 세트와 일부 중복될 수 있습니다.
- `schemas/eval_case.schema.json`: 구조화 평가 케이스의 JSON Schema입니다.
- `baselines/`: 코드와 모델 및 RAG 설정별 기준 점수를 보관합니다.
- `results/`: 로컬 평가 실행 결과를 저장합니다. 생성 결과는 Git에 커밋하지 않습니다.
- `drafts/question_bank_100.jsonl`: 유형 균형을 맞춘 100개 질문 초안입니다. 정답 출처와 사실 라벨을 검수하기 전에는 실행용 회귀 세트로 사용하지 않습니다.

## 실행 예시

```powershell
# 빠른 확인
.\.venv\Scripts\python.exe -m eval.tools.eval_ask --questions eval\cases\smoke.jsonl --output eval\results\smoke.jsonl

# 빠른 smoke 평가 (기본값)
.\.venv\Scripts\python.exe -m eval.tools.eval_ask

# 실패 경계 집중 평가
.\.venv\Scripts\python.exe -m eval.tools.eval_ask --questions eval\cases\challenge.jsonl --output eval\results\challenge.jsonl
```

기본 평가 입력은 빠른 확인용 `smoke.jsonl`입니다. 전체 평가에는 검수를 마친
`regression.jsonl`을 지정합니다.

## 100개 질문 초안

질문 초안은 `draft_question.schema.json` 형식이며, `case_type`,
`scenario_tags`, `difficulty`, `label_status`, `origin`을 포함합니다.
`origin=existing`은 기존 regression 질문과 정확히 일치하는 질문이고,
`origin=new`는 유형 균형을 위해 추가한 질문입니다.

```powershell
# 원본 정의로 질문 뱅크 재생성
.\.venv\Scripts\python.exe -m eval.tools.build_eval_question_bank

# 수량, 유형 분포, 중복, 필수 시나리오 검사
.\.venv\Scripts\python.exe -m eval.tools.audit_eval_question_bank
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
.\.venv\Scripts\python.exe -m eval.tools.audit_regression_labels

# 질문 뱅크로부터 빈 서식을 다시 만들기(기존 입력 내용이 사라짐)
.\.venv\Scripts\python.exe -m eval.tools.build_regression_template
```

두 번째 명령은 `regression.jsonl`을 초기화하므로 최초 생성 또는 명시적인
재초기화가 필요할 때만 사용합니다.

## Layout-aware OCR RAG evaluation

이 평가는 격리된 로컬 테스트 저장소를 사용하며 Supabase를 변경하지 않습니다.

```powershell
python -m scripts.rag.ingest_layout_data      # files/_reviewed/body_image_ocr/*.reviewed.jsonl -> files/_test_db/test_layout_rag_YYYYMMDD/
python -m eval.tools.generate_testset         # 최대 50개 QA 쌍 -> tests/eval_dataset.csv (--mode rule: API 비용 없음)
python -m eval.tools.run_evaluation           # eval_report.md (+ .json) (--retrieval-only: API 비용 없음)
```

표는 각 청크에 헤더를 반복하는 Markdown으로 나누고, 다이어그램은 문장으로 바꿉니다.
모든 청크는 원본 경로, 이미지 해시, 검수 및 추출 출처를 `provenance`에 보관합니다.
테스트 저장소 이름은 `test_layout_rag_YYYYMMDD[_suffix]` 형식을 사용합니다.
`eval/tools/llm_budget.py`는 호출당 최대 3회 시도, 실행별 요청 수 상한,
400/401/403/404 응답 재시도 금지를 적용합니다.
`scripts/tests/fixtures/layout_rag/`에 같은 스키마의 smoke 테스트 입력이 있습니다.

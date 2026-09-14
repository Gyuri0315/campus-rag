# Evaluation Commands

### `scripts/eval_ask.py`

`eval/questions.jsonl`의 질문을 backend `/ask` API로 반복 호출하고 결과를 JSONL로 저장합니다.
기본 API URL은 `http://localhost:8000`이며, `--base-url` 인자 또는 `ASK_API_BASE_URL`/`API_BASE_URL` 환경변수로 바꿀 수 있습니다.

```powershell
.\.venv\Scripts\python.exe scripts\eval_ask.py
.\.venv\Scripts\python.exe scripts\eval_ask.py --base-url http://localhost:8000 --output outputs\eval_results.jsonl
$env:ASK_API_BASE_URL="http://localhost:8000"; .\.venv\Scripts\python.exe scripts\eval_ask.py --limit 5
```

주요 옵션:

- `--questions PATH`: 입력 질문 JSONL 경로입니다. 기본값은 `eval/questions.jsonl`입니다.
- `--output PATH`: 결과 JSONL 저장 경로입니다. 기본값은 `outputs/eval_results.jsonl`입니다.
- `--timeout N`: 질문별 API timeout 초입니다.
- `--limit N`: 앞에서부터 N개 질문만 평가합니다.
- `--sleep N`: 요청 사이에 N초 대기합니다.

결과에는 `question`, `category`, `answer`, `sources`, `source_count`, `top_similarity`와 API 상태/오류 정보가 저장됩니다. 개별 API 오류가 발생해도 다음 질문 평가를 계속합니다.

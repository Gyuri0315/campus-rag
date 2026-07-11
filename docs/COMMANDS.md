# Commands

프로젝트 루트(`D:\Git\campus-rag`)에서 실행하는 것을 기준으로 정리합니다.
PowerShell에서는 가급적 프로젝트 가상환경의 Python을 직접 사용합니다.

```powershell
.\.venv\Scripts\python.exe --version
```

로그를 바로 보고 싶으면 `-u` 옵션을 붙입니다.

```powershell
.\.venv\Scripts\python.exe -u scripts\rule\crawler.py --laws --bylaws
```

실시간 로그 확인 예시:

```powershell
Get-Content logs\rule_crawler.log -Wait -Tail 30
Get-Content logs\rule_preprocessing.log -Wait -Tail 30
Get-Content logs\main_notice_crawler.log -Wait -Tail 30
Get-Content logs\main_student_life_crawler.log -Wait -Tail 30
```

## Main Website

### `scripts/main/notice_crawler.py`

부경대학교 메인 홈페이지 공지사항(`/main/163`)을 크롤링합니다.

출력:

- `files/pknu_notice/output/json/<category>/<slug>.json`
- `files/pknu_notice/output/html/<category>/<slug>.html`
- `files/pknu_notice/output/files/<category>/<slug>/<attachment>`
- `files/pknu_notice/output/deleted/<category>/<slug>.json`

첨부파일은 `output/files` 아래에 저장되며, JSON `attachments`에는 `saved_path` 등 로컬 저장 메타데이터가 함께 기록됩니다.

```powershell
.\.venv\Scripts\python.exe scripts\main\notice_crawler.py
.\.venv\Scripts\python.exe scripts\main\notice_crawler.py --recent-only 5
```

옵션:

- `--full-resync`: 전체 페이지를 다시 수집합니다.
- `--recent-only N`: 카테고리별 최근 N페이지만 목록 수집합니다.
- `--reset-state`: `state_pknu_notice.json`을 삭제하고 다시 시작합니다.
- `--once`: 1회 실행합니다. 현재 기본 동작과 같습니다.
- `--only-cd CODE`: 특정 공지 카테고리 코드만 수집합니다. 예: `10001`

### `scripts/main/student_life_crawler.py`

부경대학교 대학생활 가이드(`/main/434`)와 E-하나로 eBook을 수집합니다.

출력:

- `files/pknu_student_life/output/json/<subcategory>/<slug>.json`
- `files/pknu_student_life/output/files/<subcategory>/<slug>/*.pdf`
- `files/pknu_student_life/output/deleted/`

```powershell
.\.venv\Scripts\python.exe scripts\main\student_life_crawler.py
.\.venv\Scripts\python.exe scripts\main\student_life_crawler.py --mode guide --limit 3
.\.venv\Scripts\python.exe scripts\main\student_life_crawler.py --mode ebook
```

옵션:

- `--mode guide|ebook|all`: `guide`는 `/main/434`, `ebook`은 E-하나로, `all`은 둘 다 수집합니다. 기본값은 `all`입니다.
- `--full-resync`: `content_hash` 비교를 무시하고 재수집합니다.
- `--reset-state`: `state_pknu_student_life.json`을 삭제합니다.
- `--limit N`: `guide` 모드에서 처리할 PDF 개수를 제한합니다. smoke test용입니다.

### Main 분석/점검 스크립트

- `scripts/main/analyze_student_life.py`: `/main/434`와 E-하나로 구조 분석용 임시 스크립트입니다.
- `scripts/main/probe_ebook.py`: E-하나로 원본 PDF 후보 경로를 빠르게 확인합니다.
- `scripts/main/student_life_stats.py`: `files/pknu_student_life/output/json` 결과의 PDF 텍스트 추출 상태를 요약합니다.

## CE Pipeline

### `scripts/ce/crawler.py`

컴퓨터·인공지능공학부 홈페이지를 크롤링합니다.
게시글 JSON, 원본 HTML, 첨부파일을 `files/ce/output` 아래에 저장합니다.

```powershell
.\.venv\Scripts\python.exe scripts\ce\crawler.py --once
```

옵션:

- `--once`: 스케줄 루프 없이 즉시 1회 실행합니다.
- `--reset-state`: `state.json`을 삭제하고 처음부터 다시 수집합니다.

### `scripts/ce/preprocessing.py`

CE 크롤링 결과를 RAG용 chunk JSON으로 전처리합니다.
게시글 JSON과 첨부파일 모두 처리할 수 있습니다.

```powershell
.\.venv\Scripts\python.exe scripts\ce\preprocessing.py
```

기본값:

- `--input-root`: `files/ce/output/files`
- `--output-root`: `files/ce/preprocessed`
- `--output-json-root`: `files/ce/output/json`
- `--chunk-size`: `900`
- `--chunk-overlap`: `120`
- `--pdf-ocr`: `auto`
- `--ocr-language`: `kor+eng`
- `--ocr-dpi`: `200`
- `--layout`: `by_ext`
- `--file-ext`: 지정하지 않으면 지원 확장자 전체

자주 쓰는 예시:

```powershell
.\.venv\Scripts\python.exe scripts\ce\preprocessing.py --input-root files\ce\output\json --output-root files\ce\preprocessed\json --output-json-root files\ce\output\json --layout flat
.\.venv\Scripts\python.exe scripts\ce\preprocessing.py --input-root files\ce\output\files --output-root files\ce\preprocessed\files --output-json-root files\ce\output\json
.\.venv\Scripts\python.exe scripts\ce\preprocessing.py --input-root files\ce\output\files --output-root files\ce\preprocessed\files --file-ext pdf hwp xls xlsx pptx zip
```

주요 옵션:

- `--input-root PATH`: 원본 입력 루트입니다.
- `--output-root PATH`: 전처리 JSON 출력 루트입니다.
- `--output-json-root PATH`: 첨부파일 provenance 조인에 사용할 크롤링 JSON 루트입니다.
- `--failed-from-log PATH`: 전처리 로그의 `[FAIL]` 항목만 다시 처리합니다.
- `--dry-run`: 저장 없이 대상 파일만 확인합니다.
- `--chunk-size N`: chunk 최대 문자 수입니다.
- `--chunk-overlap N`: 인접 chunk 간 overlap 문자 수입니다.
- `--pdf-ocr auto|never|always`: PDF OCR fallback 정책입니다.
- `--ocr-language LANG`: Tesseract OCR 언어입니다.
- `--ocr-dpi N`: OCR 렌더링 DPI입니다.
- `--layout by_ext|flat`: 출력 경로 구조를 선택합니다.
- `--file-ext EXT [EXT ...]`: 지정한 확장자만 전처리합니다. `pdf hwp xls xlsx pptx zip`처럼 공백으로 나열하거나 `pdf,hwp,xls,xlsx,pptx,zip`처럼 쉼표로 나열할 수 있으며, `.pdf`처럼 점을 붙여도 됩니다.

### `scripts/rag/preprocess_files.py`

학과/데이터셋과 무관하게 파일 루트를 지정해 첨부파일을 RAG용 chunk JSON으로 전처리합니다.
CE 외의 다른 학과 크롤링 결과도 같은 JSON/파일 구조를 쓰면 이 명령을 그대로 사용할 수 있습니다.

```powershell
.\.venv\Scripts\python.exe scripts\rag\preprocess_files.py --input-root files\ce\output\files --output-root files\ce\preprocessed\files --output-json-root files\ce\output\json
.\.venv\Scripts\python.exe scripts\rag\preprocess_files.py --input-root files\cse\output\files --output-root files\cse\preprocessed\files --output-json-root files\cse\output\json
.\.venv\Scripts\python.exe scripts\rag\preprocess_files.py --input-root files\cse\output\files --output-root files\cse\preprocessed\files --file-ext pdf hwp xls xlsx pptx zip
```

주요 옵션은 `scripts/ce/preprocessing.py`의 파일 전처리 옵션과 같습니다. `--output-json-root`가 없거나 비어 있으면 게시글 메타데이터 없이 파일 자체 provenance로 처리합니다.

### `scripts/ce/update_priorities.py`

Supabase의 CE source priority score를 갱신합니다.

```powershell
.\.venv\Scripts\python.exe scripts\ce\update_priorities.py
.\.venv\Scripts\python.exe scripts\ce\update_priorities.py --dry-run
```

## Rule Pipeline

### `scripts/rule/crawler.py`

부경대학교 규정집을 크롤링합니다.
규정 JSON, HTML, 첨부파일, 첨부파일 텍스트 추출 메타데이터를 `files/rule/output` 아래에 저장합니다.

```powershell
.\.venv\Scripts\python.exe scripts\rule\crawler.py --laws --bylaws
```

옵션:

- `--laws`: 학칙/규정 트리 항목을 수집합니다.
- `--bylaws`: 지침/세칙 목록과 상세 페이지를 수집합니다.
- `--download-files`: 첨부파일을 다운로드합니다. 기본값입니다.
- `--no-download-files`: 첨부파일 다운로드와 첨부파일 텍스트 추출을 생략합니다.
- `--max-law-items N`: smoke test용으로 학칙/규정 항목 수를 제한합니다.
- `--max-bylaw-pages N`: smoke test용으로 지침/세칙 목록 페이지 수를 제한합니다.

예시:

```powershell
.\.venv\Scripts\python.exe scripts\rule\crawler.py --laws --max-law-items 1
.\.venv\Scripts\python.exe scripts\rule\crawler.py --bylaws --max-bylaw-pages 1
.\.venv\Scripts\python.exe scripts\rule\crawler.py --laws --bylaws --no-download-files
```

### `scripts/rule/preprocessing.py`

규정집 크롤링 산출물을 RAG용 chunk JSON으로 전처리합니다.
JSON, HTML, 다운로드된 첨부파일을 모두 처리할 수 있습니다.

```powershell
.\.venv\Scripts\python.exe scripts\rule\preprocessing.py
```

기본값:

- `--json-root`: `files/rule/output/json`
- `--html-root`: `files/rule/output/html`
- `--files-root`: `files/rule/output/files`
- `--output-root`: `files/rule/preprocessed`
- `--source-scope`: `all`
- `--chunk-size`: `900`
- `--chunk-overlap`: `120`
- `--file-ext`: 지정하지 않으면 지원 첨부파일 확장자 전체

출력 구조:

```text
files/rule/preprocessed/json/
files/rule/preprocessed/html/
files/rule/preprocessed/file/
```

주요 옵션:

- `--source-scope json|html|files|all`: 전처리할 산출물 종류입니다.
- `--failed-from-log PATH`: 전처리 로그에서 `[FAIL:file]` 경로만 파싱해 다시 처리합니다.
- `--dry-run`: 저장 없이 대상만 확인합니다.
- `--chunk-size N`: chunk 최대 문자 수입니다.
- `--chunk-overlap N`: 인접 chunk 간 overlap 문자 수입니다.
- `--file-ext EXT [EXT ...]`: `--source-scope files` 또는 `all`에서 지정한 첨부파일 확장자만 전처리합니다. `pdf hwp xls xlsx pptx` 또는 `pdf,hwp,xls,xlsx,pptx` 형식을 사용할 수 있습니다.

예시:

```powershell
.\.venv\Scripts\python.exe scripts\rule\preprocessing.py --source-scope json
.\.venv\Scripts\python.exe scripts\rule\preprocessing.py --source-scope html
.\.venv\Scripts\python.exe scripts\rule\preprocessing.py --source-scope files
.\.venv\Scripts\python.exe scripts\rule\preprocessing.py --source-scope files --file-ext pdf hwp xls xlsx pptx
.\.venv\Scripts\python.exe scripts\rule\preprocessing.py --source-scope files --file-ext pdf,hwp,xls,xlsx,pptx
.\.venv\Scripts\python.exe scripts\rule\preprocessing.py --failed-from-log logs\rule_preprocessing.log
```

### `scripts/rule/update_priorities.py`

Supabase의 rule source priority score를 갱신합니다.

```powershell
.\.venv\Scripts\python.exe scripts\rule\update_priorities.py
.\.venv\Scripts\python.exe scripts\rule\update_priorities.py --dry-run
```

옵션:

- `--dry-run`: DB 업데이트 없이 계산 결과만 확인합니다.
- `--from-index`: 로컬 `files/rule/vectorized/index.jsonl`에서 preview합니다. `--dry-run`과 함께 사용해야 합니다.
- `--index-path PATH`: preview에 사용할 index 경로입니다.
- `--preview-limit N`: preview 출력 개수입니다.

## RAG

### `scripts/rag/vectorization.py`

전처리된 chunk JSON을 embedding 벡터로 변환하고 `index.jsonl`, `manifest.json`, 개별 vector 파일을 생성합니다.

```powershell
.\.venv\Scripts\python.exe scripts\rag\vectorization.py --dataset ce --backend sentence-transformers
.\.venv\Scripts\python.exe scripts\rag\vectorization.py --dataset rule --backend sentence-transformers
```

기본값:

- `--dataset`: `ce`
- `--backend`: `hash`
- `--model-name`: `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`
- `--dimensions`: `768`
- `--batch-size`: `32`

주요 옵션:

- `--dataset ce|rule`: 기본 입출력 경로 preset입니다.
- `--input-root PATH`: 전처리 JSON 루트입니다. 지정하면 `--dataset` 입력 경로보다 우선합니다.
- `--output-root PATH`: 벡터화 출력 루트입니다. 지정하면 `--dataset` 출력 경로보다 우선합니다.
- `--backend hash|sentence-transformers`: embedding backend입니다.
- `--model-name NAME`: sentence-transformers 모델명입니다.
- `--dimensions N`: hash backend 차원 수입니다.
- `--batch-size N`: embedding batch size입니다.
- `--dry-run`: 저장 없이 대상만 확인합니다.

Supabase 적재 전에는 DB schema와 맞는 384차원 `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` 사용을 권장합니다.

### `scripts/rag/load_to_supabase.py`

벡터화된 `index.jsonl`을 Supabase PostgreSQL에 적재합니다.

```powershell
.\.venv\Scripts\python.exe scripts\rag\load_to_supabase.py --dataset ce
.\.venv\Scripts\python.exe scripts\rag\load_to_supabase.py --dataset rule
```

기본 매핑:

- `--dataset ce`: `files/ce/vectorized/index.jsonl` -> `rag_sources`, `rag_chunks`
- `--dataset rule`: `files/rule/vectorized/index.jsonl` -> `rule_sources`, `rule_chunks`
- `--batch-size`: `200`

옵션:

- `--index PATH`: 직접 지정할 vectorized JSONL 경로입니다.
- `--dataset ce|rule`: Supabase table preset입니다.
- `--sources-table NAME`: source table명을 직접 지정합니다.
- `--chunks-table NAME`: chunk table명을 직접 지정합니다.
- `--batch-size N`: DB upsert batch size입니다.

필요 환경 변수는 `backend/.env`에서 로드합니다.

- `DATABASE_URL` 또는 `SUPABASE_DB_URL`
- 또는 `PGHOST`, `PGDATABASE`, `PGUSER`, `PGPASSWORD`
- 선택: `PGPORT`, `PGSSLMODE`

### `scripts/rag/query_supabase.py`

Supabase에 적재된 RAG chunk를 대상으로 semantic search를 실행합니다.

```powershell
.\.venv\Scripts\python.exe scripts\rag\query_supabase.py "졸업 요건 알려줘" --dataset ce
.\.venv\Scripts\python.exe scripts\rag\query_supabase.py "학칙의 휴학 규정 알려줘" --dataset rule
```

기본값:

- `--dataset`: `ce`
- `--model-name`: `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`
- `--top-k`: `5`
- `--min-similarity`: `0.0`
- `--rank-by`: `similarity`
- `--priority-weight`: `0.15`

옵션:

- positional `question`: 검색 질문입니다. `--rank-by priority`에서는 생략할 수 있습니다.
- `--dataset ce|rule`: 검색할 dataset입니다.
- `--match-function NAME`: 기본 RPC 함수명을 override합니다. 기본 similarity 검색에서만 사용합니다.
- `--top-k N`: 반환 결과 수입니다.
- `--min-similarity FLOAT`: 최소 similarity입니다.
- `--rank-by similarity|priority|hybrid`: 정렬 기준입니다.
- `--include-priority`: similarity 검색에서도 priority 정보를 함께 출력합니다.
- `--priority-weight FLOAT`: hybrid 정렬에서 priority 반영 비율입니다.

### `scripts/rag/search_smoke.py`

`query_supabase.py`를 여러 고정 질문으로 호출하는 smoke test wrapper입니다.
검색 로직은 없고, 검색 파이프라인이 대략 정상 동작하는지 빠르게 확인하는 용도입니다.

```powershell
.\.venv\Scripts\python.exe scripts\rag\search_smoke.py
```

### `scripts/rag/pipelining.py`

CE 파이프라인을 자동으로 실행합니다.

실행 순서:

1. `scripts/ce/crawler.py --once`
2. `scripts/ce/preprocessing.py`로 JSON 전처리
3. `scripts/ce/preprocessing.py`로 첨부파일 전처리
4. `scripts/rag/vectorization.py`
5. `scripts/rag/load_to_supabase.py`
6. `scripts/ce/update_priorities.py`

1회 실행:

```powershell
.\.venv\Scripts\python.exe scripts\rag\pipelining.py --once
```

스케줄러 실행:

```powershell
.\.venv\Scripts\python.exe scripts\rag\pipelining.py --run-at 09:00
```

옵션:

- `--once`: 스케줄러 없이 전체 CE 파이프라인을 1회 실행합니다.
- `--run-on-start`: 시작 즉시 1회 실행한 뒤 스케줄러를 유지합니다. 기본값입니다.
- `--no-run-on-start`: 즉시 실행 없이 스케줄러만 시작합니다.
- `--run-at HH:MM`: 매일 실행 시각입니다. 기본값은 `09:00`입니다.
- `--poll-seconds N`: 스케줄러 polling 간격입니다.
- `--vector-backend sentence-transformers|hash`: vectorization backend입니다.
- `--vector-batch-size N`: vectorization batch size입니다.
- `--load-batch-size N`: Supabase 적재 batch size입니다.

## Supabase

### Migrations

마이그레이션 SQL은 `supabase/migrations/`에 있습니다.

```text
supabase/migrations/
  001_create_rag_documents.sql
  002_split_rag_documents.sql
  003_chat_history.sql
  004_create_rule_rag_tables.sql
  005_add_document_priority_columns.sql
```

### Audit

DB 상태 점검용 읽기 전용 스크립트는 `supabase/audit/`에 있습니다.

```powershell
.\.venv\Scripts\python.exe supabase\audit\audit_db.py
.\.venv\Scripts\python.exe supabase\audit\audit_db_extra.py
```

## Common Utilities

### `scripts/text_cleaning.py`

전처리 공통 텍스트 정리 함수입니다. 단독 CLI보다는 preprocessing 코드에서 import해 사용하는 helper입니다.

## Common End-To-End Examples

### CE

```powershell
.\.venv\Scripts\python.exe scripts\ce\crawler.py --once
.\.venv\Scripts\python.exe scripts\ce\preprocessing.py --input-root files\ce\output\json --output-root files\ce\preprocessed\json --output-json-root files\ce\output\json --layout flat
.\.venv\Scripts\python.exe scripts\ce\preprocessing.py --input-root files\ce\output\files --output-root files\ce\preprocessed\files --output-json-root files\ce\output\json
.\.venv\Scripts\python.exe scripts\rag\vectorization.py --dataset ce --backend sentence-transformers
.\.venv\Scripts\python.exe scripts\rag\load_to_supabase.py --dataset ce
.\.venv\Scripts\python.exe scripts\ce\update_priorities.py
```

### Rule

```powershell
.\.venv\Scripts\python.exe scripts\rule\crawler.py --laws --bylaws
.\.venv\Scripts\python.exe scripts\rule\preprocessing.py
.\.venv\Scripts\python.exe scripts\rag\vectorization.py --dataset rule --backend sentence-transformers
.\.venv\Scripts\python.exe scripts\rag\load_to_supabase.py --dataset rule
.\.venv\Scripts\python.exe scripts\rule\update_priorities.py
```

### Main Website

현재 main website 산출물(`pknu_notice`, `pknu_student_life`)은 `scripts/rag/vectorization.py --dataset` preset에 아직 포함되어 있지 않습니다.
RAG 파이프라인에 포함하려면 별도 preprocessing/vectorization preset 추가가 필요합니다.

```powershell
.\.venv\Scripts\python.exe scripts\main\notice_crawler.py --recent-only 5
.\.venv\Scripts\python.exe scripts\main\student_life_crawler.py --mode all
```

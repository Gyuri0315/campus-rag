# 명령어 정리

이 문서는 프로젝트의 실행 스크립트별 역할과 자주 사용하는 CLI 옵션을 정리합니다.
모든 명령은 저장소 루트에서 실행하는 것을 기준으로 합니다.

## 권장 실행 환경

```powershell
.\.venv\Scripts\python.exe --version
```

터미널에 로그가 바로 보이게 실행하고 싶으면 `-u` 옵션을 사용합니다.

```powershell
.\.venv\Scripts\python.exe -u scripts/rule/crawler.py --laws --bylaws
```

로그를 실시간으로 확인하려면 다음 명령을 사용합니다.

```powershell
Get-Content logs\rule_crawler.log -Wait -Tail 30
Get-Content logs\rule_preprocessing.log -Wait -Tail 30
```

## 컴퓨터공학부 파이프라인

### `scripts/ce/crawler.py`

컴퓨터·인공지능공학부 홈페이지를 크롤링합니다. 크롤링된 JSON, HTML, 첨부파일은 `files/ce/output` 아래에 저장됩니다.

```powershell
python scripts/ce/crawler.py --once
```

옵션:

- `--once`: 스케줄링 없이 즉시 1회만 실행합니다.
- `--reset-state`: `state.json`을 초기화하고 처음부터 다시 수집합니다.

### `scripts/ce/preprocessing.py`

컴퓨터공학부 크롤링 결과와 다운로드된 첨부파일을 RAG용 청크 JSON으로 전처리합니다.

```powershell
python scripts/ce/preprocessing.py
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

옵션:

- `--input-root PATH`: 원본 첨부파일 루트 경로입니다.
- `--output-root PATH`: 전처리 JSON 출력 루트 경로입니다.
- `--output-json-root PATH`: 첨부파일 provenance 조인에 사용할 크롤링 JSON 루트입니다.
- `--dry-run`: 실제 저장 없이 처리 대상만 확인합니다.
- `--chunk-size N`: 청크 최대 문자 수입니다.
- `--chunk-overlap N`: 연속 청크 사이의 문자 오버랩 길이입니다.
- `--pdf-ocr auto|never|always`: PDF OCR fallback 사용 방식입니다.
- `--ocr-language LANG`: Tesseract OCR 언어 설정입니다.
- `--ocr-dpi N`: PDF 페이지를 OCR 이미지로 렌더링할 DPI입니다.

## 규정집 파이프라인

### `scripts/rule/crawler.py`

국립부경대학교 규정집을 크롤링합니다. 규정 JSON, 원본 HTML, 첨부파일, 첨부파일 텍스트 추출 메타데이터를 `files/rule/output` 아래에 저장합니다.

```powershell
python scripts/rule/crawler.py --laws --bylaws
```

옵션:

- `--laws`: 학칙/규정 트리의 항목을 크롤링합니다.
- `--bylaws`: 지침/세칙 목록과 상세 페이지를 크롤링합니다.
- `--download-files`: 첨부파일을 다운로드합니다. 기본값입니다.
- `--no-download-files`: 첨부파일 다운로드와 첨부파일 텍스트 추출을 생략합니다.
- `--max-law-items N`: 테스트용으로 학칙/규정 항목 수를 제한합니다.
- `--max-bylaw-pages N`: 테스트용으로 지침/세칙 목록 페이지 수를 제한합니다.

예시:

```powershell
python scripts/rule/crawler.py --laws --max-law-items 1
python scripts/rule/crawler.py --bylaws --max-bylaw-pages 1
python scripts/rule/crawler.py --laws --bylaws --no-download-files
```

### `scripts/rule/preprocessing.py`

규정집 크롤링 산출물을 전처리합니다. JSON, HTML, 다운로드된 첨부파일을 모두 처리할 수 있습니다. 중복 텍스트는 허용하며, 출력 메타데이터의 `source_kind`로 `json`, `html`, `file` 출처를 구분합니다.

```powershell
python scripts/rule/preprocessing.py
```

기본값:

- `--json-root`: `files/rule/output/json`
- `--html-root`: `files/rule/output/html`
- `--files-root`: `files/rule/output/files`
- `--output-root`: `files/rule/preprocessed`
- `--source-scope`: `all`
- `--chunk-size`: `900`
- `--chunk-overlap`: `120`

출력 구조:

```text
files/rule/preprocessed/json/
files/rule/preprocessed/html/
files/rule/preprocessed/file/
```

옵션:

- `--json-root PATH`: 규정집 크롤링 JSON 루트입니다.
- `--html-root PATH`: 규정집 원본 HTML 루트입니다.
- `--files-root PATH`: 규정집 다운로드 첨부파일 루트입니다.
- `--output-root PATH`: 전처리 출력 루트입니다.
- `--source-scope json|html|files|all`: 전처리할 산출물 종류를 선택합니다.
- `--failed-from-log PATH`: 전처리 로그에서 `[FAIL:file]` 경로만 파싱해 실패한 첨부파일만 재처리합니다.
- `--dry-run`: 실제 저장 없이 처리 대상만 확인합니다.
- `--chunk-size N`: 청크 최대 문자 수입니다.
- `--chunk-overlap N`: 연속 청크 사이의 문자 오버랩 길이입니다.

예시:

```powershell
python scripts/rule/preprocessing.py --source-scope json
python scripts/rule/preprocessing.py --source-scope html
python scripts/rule/preprocessing.py --source-scope files
python scripts/rule/preprocessing.py --failed-from-log logs\rule_preprocessing.log
```

## 벡터화

### `scripts/rag/vectorization.py`

전처리된 청크 JSON을 벡터화하고, 개별 벡터 파일과 `index.jsonl`을 생성합니다.

컴퓨터공학부 기본 실행:

```powershell
python scripts/rag/vectorization.py --dataset ce --backend sentence-transformers
```

규정집 파이프라인 실행:

```powershell
python scripts/rag/vectorization.py --dataset rule --backend sentence-transformers
```

기본값:

- `--dataset`: `ce`
- `--dataset ce`: `files/ce/preprocessed` -> `files/ce/vectorized`
- `--dataset rule`: `files/rule/preprocessed` -> `files/rule/vectorized`
- `--backend`: `hash`
- `--model-name`: `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`
- `--dimensions`: `768` (`hash` backend 사용 시)
- `--batch-size`: `32`

옵션:

- `--input-root PATH`: 전처리 JSON 루트입니다.
- `--output-root PATH`: 벡터화 결과 출력 루트입니다.
- `--dataset ce|rule`: 벡터화할 데이터셋 기본 경로를 선택합니다. `--input-root`, `--output-root`를 직접 지정하면 해당 경로가 우선합니다.
- `--backend hash|sentence-transformers`: 임베딩 backend를 선택합니다.
- `--model-name NAME`: `sentence-transformers` backend 사용 시 모델명입니다.
- `--dimensions N`: `hash` backend 사용 시 임베딩 차원 수입니다.
- `--batch-size N`: 임베딩 배치 크기입니다.
- `--dry-run`: 실제 저장 없이 처리 대상만 확인합니다.

Supabase 적재 전에는 `sentence-transformers` backend를 사용하는 것이 좋습니다. 현재 DB 스키마는 384차원 임베딩을 기준으로 합니다.

## Supabase 적재

### `scripts/rag/load_to_supabase.py`

벡터화된 RAG 청크를 Supabase PostgreSQL 테이블에 적재합니다.

컴퓨터공학부 기본 실행:

```powershell
python scripts/rag/load_to_supabase.py --dataset ce
```

규정집 파이프라인 실행:

```powershell
python scripts/rag/load_to_supabase.py --dataset rule
```

기본값:

- `--dataset`: `ce`
- `--dataset ce`: `files/ce/vectorized/index.jsonl` -> `rag_sources/rag_chunks`
- `--dataset rule`: `files/rule/vectorized/index.jsonl` -> `rule_sources/rule_chunks`
- `--batch-size`: `200`

옵션:

- `--dataset ce|rule`: 적재할 Supabase 테이블 preset을 선택합니다. `ce`는 `rag_sources/rag_chunks`, `rule`은 `rule_sources/rule_chunks`를 사용합니다.
- `--index PATH`: 벡터화된 JSONL 인덱스 경로입니다. 지정하지 않으면 `--dataset`에 맞는 기본 index를 사용합니다.
- `--sources-table NAME`: `public` schema 안의 source 테이블명을 직접 지정합니다.
- `--chunks-table NAME`: `public` schema 안의 chunk 테이블명을 직접 지정합니다.
- `--batch-size N`: DB insert 배치 크기입니다.

필요한 환경 변수:

- `DATABASE_URL` 또는 `SUPABASE_DB_URL`
- 또는 `PGHOST`, `PGDATABASE`, `PGUSER`, `PGPASSWORD`
- 선택: `PGPORT`, `PGSSLMODE`

환경 변수는 `backend/.env`에서 로드됩니다.

## Supabase 검색

### `scripts/rag/query_supabase.py`

Supabase에 적재된 RAG 청크를 대상으로 semantic search를 실행합니다.

```powershell
python scripts/rag/query_supabase.py "졸업 요건 알려줘" --dataset ce
python scripts/rag/query_supabase.py "학칙의 휴학 규정을 알려줘" --dataset rule
```

기본값:

- `--model-name`: `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`
- `--dataset`: `ce`
- `--top-k`: `5`
- `--min-similarity`: `0.0`

옵션:

- positional `question`: 검색 질문입니다.
- `--model-name NAME`: 질문 임베딩에 사용할 모델명입니다.
- `--top-k N`: 반환할 검색 결과 수입니다.
- `--min-similarity FLOAT`: 최소 유사도 기준입니다.

## 컴퓨터공학부 스케줄 파이프라인

### `scripts/rag/pipelining.py`

`scripts/ce/crawler.py -> scripts/ce/preprocessing.py -> scripts/rag/vectorization.py -> scripts/rag/load_to_supabase.py` 순서로 실행합니다.
이 스크립트는 컴퓨터공학부 파이프라인을 대상으로 하며, 규정집 파이프라인용은 아닙니다.

1회 실행:

```powershell
python scripts/rag/pipelining.py --once
```

스케줄러 실행:

```powershell
python scripts/rag/pipelining.py --run-at 09:00
```

기본값:

- `--run-at`: `09:00`
- `--poll-seconds`: `30`
- `--vector-backend`: `sentence-transformers`
- `--vector-batch-size`: `32`
- `--load-batch-size`: `200`
- 기본적으로 스케줄러 시작 시 1회 즉시 실행합니다. 즉시 실행을 막으려면 `--no-run-on-start`를 사용합니다.

옵션:

- `--once`: 스케줄러 없이 전체 파이프라인을 1회 실행합니다.
- `--run-on-start`: 시작 즉시 1회 실행한 뒤 스케줄러를 유지합니다. 기본값입니다.
- `--no-run-on-start`: 즉시 실행 없이 스케줄러만 시작합니다.
- `--run-at HH:MM`: 매일 실행할 시각입니다.
- `--poll-seconds N`: 스케줄러 polling 간격입니다.
- `--vector-backend sentence-transformers|hash`: `scripts/rag/vectorization.py`에 넘길 backend입니다.
- `--vector-batch-size N`: `scripts/rag/vectorization.py`에 넘길 배치 크기입니다.
- `--load-batch-size N`: `scripts/rag/load_to_supabase.py`에 넘길 배치 크기입니다.

## 자주 쓰는 전체 실행 명령

### 컴퓨터공학부

```powershell
python scripts/ce/crawler.py --once
python scripts/ce/preprocessing.py
python scripts/rag/vectorization.py --dataset ce --backend sentence-transformers
python scripts/rag/load_to_supabase.py --dataset ce
```

### 규정집

```powershell
python scripts/rule/crawler.py --laws --bylaws
python scripts/rule/preprocessing.py
python scripts/rag/vectorization.py --dataset rule --backend sentence-transformers
python scripts/rag/load_to_supabase.py --dataset rule
```

### 규정집 실패 첨부파일 재처리

```powershell
python scripts/rule/preprocessing.py --failed-from-log logs\rule_preprocessing.log
```

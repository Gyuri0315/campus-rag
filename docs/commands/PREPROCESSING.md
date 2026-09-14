# Preprocessing Commands

## CE

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

## Shared File Preprocessor

### `scripts/rag/preprocess_files.py`

학과/데이터셋과 무관하게 파일 루트를 지정해 첨부파일을 RAG용 chunk JSON으로 전처리합니다.
CE 외의 다른 학과 크롤링 결과도 같은 JSON/파일 구조를 쓰면 이 명령을 그대로 사용할 수 있습니다.

```powershell
.\.venv\Scripts\python.exe scripts\rag\preprocess_files.py --input-root files\ce\output\files --output-root files\ce\preprocessed\files --output-json-root files\ce\output\json
.\.venv\Scripts\python.exe scripts\rag\preprocess_files.py --input-root files\cse\output\files --output-root files\cse\preprocessed\files --output-json-root files\cse\output\json
.\.venv\Scripts\python.exe scripts\rag\preprocess_files.py --input-root files\cse\output\files --output-root files\cse\preprocessed\files --file-ext pdf hwp xls xlsx pptx zip
```

주요 옵션은 `scripts/ce/preprocessing.py`의 파일 전처리 옵션과 같습니다. `--output-json-root`가 없거나 비어 있으면 게시글 메타데이터 없이 파일 자체 provenance로 처리합니다.

## Rule

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

## Common Utilities

### `scripts/text_cleaning.py`

전처리 공통 텍스트 정리 함수입니다. 단독 CLI보다는 preprocessing 코드에서 import해 사용하는 helper입니다.

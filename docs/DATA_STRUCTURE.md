# Data Structure

크롤링 산출물, 전처리 결과, 벡터화 결과는 기본적으로 `files/` 아래에 저장합니다.
`files/`는 `.gitignore` 대상이며, 재생성 가능한 데이터 저장소로 취급합니다.

## Top-Level Layout

```text
files/
  ce/
    output/
      json/        # CE crawler page JSON
      html/        # CE crawler raw HTML
      files/       # CE downloaded attachments
    preprocessed/  # CE RAG chunks
    vectorized/    # CE embeddings, index.jsonl, manifest.json

  rule/
    output/
      json/        # Rule crawler page JSON
      html/        # Rule crawler raw HTML
      files/       # Rule downloaded attachments
    preprocessed/  # Rule RAG chunks
    vectorized/    # Rule embeddings, index.jsonl, manifest.json

  pknu_notice/
    output/
      json/        # Main homepage notice JSON
      html/        # Main homepage notice raw HTML
      deleted/     # Missing/orphaned notice JSON snapshots

  pknu_student_life/
    output/
      json/        # Student life guide/eBook JSON
      files/       # Downloaded guide PDFs
      deleted/     # Reserved for deleted/orphaned items
```

## CE Dataset

Source crawler:

```powershell
.\.venv\Scripts\python.exe scripts\ce\crawler.py --once
```

Raw output:

```text
files/ce/output/json/<category>/<slug>.json
files/ce/output/html/<category>/<slug>.html
files/ce/output/files/<category>/<slug>/<attachment>
```

Preprocessing can be run separately for page JSON and attachments.

```powershell
.\.venv\Scripts\python.exe scripts\ce\preprocessing.py --input-root files\ce\output\json --output-root files\ce\preprocessed\json --output-json-root files\ce\output\json --layout flat
.\.venv\Scripts\python.exe scripts\ce\preprocessing.py --input-root files\ce\output\files --output-root files\ce\preprocessed\files --output-json-root files\ce\output\json
```

Typical preprocessed layout:

```text
files/ce/preprocessed/json/<category>/<slug>.json
files/ce/preprocessed/files/<ext>/<category>/<slug>/<attachment>.json
```

Vectorized output:

```text
files/ce/vectorized/
  index.jsonl
  manifest.json
  ...
```

Supabase target tables:

- `rag_sources`
- `rag_chunks`

## Rule Dataset

Source crawler:

```powershell
.\.venv\Scripts\python.exe scripts\rule\crawler.py --laws --bylaws
```

Rule attachments are downloaded by default. Use `--no-download-files` for a metadata-only crawl.

Raw output:

```text
files/rule/output/json/pknu_rule_law/*.json
files/rule/output/json/pknu_rule_bylaw/*.json
files/rule/output/html/pknu_rule_law/*.html
files/rule/output/html/pknu_rule_bylaw/*.html
files/rule/output/files/...
```

Rule JSON documents may include these source-specific fields:

- `content`: combined text used by downstream preprocessing.
- `html_text`: text extracted from crawled HTML.
- `page_content`: text visible in the detail page body.
- `preview_content`: preview HTML text when the site exposes it separately.
- `attachment_texts`: text extracted from downloaded attachments, including per-file extraction errors.

Preprocessed output:

```text
files/rule/preprocessed/json/
files/rule/preprocessed/html/
files/rule/preprocessed/file/
```

Every chunk keeps `source_kind` metadata so JSON, HTML, and attachment-derived records can be distinguished even when text overlaps.

Vectorized output:

```text
files/rule/vectorized/
  index.jsonl
  manifest.json
  ...
```

Supabase target tables:

- `rule_sources`
- `rule_chunks`

## Main Notice Dataset

Source crawler:

```powershell
.\.venv\Scripts\python.exe scripts\main\notice_crawler.py
```

Raw output:

```text
files/pknu_notice/output/json/<category>/<slug>.json
files/pknu_notice/output/html/<category>/<slug>.html
files/pknu_notice/output/files/<category>/<slug>/<attachment>
files/pknu_notice/output/deleted/<category>/<slug>.json
```

Notice JSON includes:

- `slug`
- `no`
- `notice_no`
- `title`
- `date`
- `url`
- `is_notice`
- `categories`
- `pknu_cd`
- `category`
- `subcategory`
- `type`
- `author`
- `content`
- `content_hash`
- `attachments`
- `source_site`
- `crawled_at`

Attachment metadata:

- `attachments` contains `{name, url, saved_path, downloaded, source_page_url, source_site, downloaded_from_url, content_type}` when a file download succeeds.
- Existing downloaded attachments are reused on incremental crawls when their `saved_path` still exists.

## Student Life Dataset

Source crawler:

```powershell
.\.venv\Scripts\python.exe scripts\main\student_life_crawler.py --mode all
```

Raw output:

```text
files/pknu_student_life/output/json/<subcategory>/<slug>.json
files/pknu_student_life/output/files/<subcategory>/<slug>/*.pdf
files/pknu_student_life/output/deleted/
```

Student life JSON includes guide/eBook metadata and, when a PDF is downloaded, an attachment entry with local file information such as `saved_path`.

Notes:

- `/main/434` guide PDFs are downloaded into `output/files`.
- Image-only or hard-to-extract PDFs may have short or skipped text content.
- `scripts/main/student_life_stats.py` summarizes text extraction status for this dataset.
- This dataset does not currently have a dedicated preprocessing/vectorization preset in `scripts/rag/vectorization.py`.

## Vectorized Index Records

`scripts/rag/vectorization.py` writes `index.jsonl` under each vectorized dataset root.
Each line represents one chunk with content, metadata, embedding, and source identity used by `scripts/rag/load_to_supabase.py`.

Supported built-in dataset presets:

- `ce`: `files/ce/preprocessed` -> `files/ce/vectorized`
- `rule`: `files/rule/preprocessed` -> `files/rule/vectorized`

`pknu_notice` and `pknu_student_life` are crawled datasets, but they are not yet included as built-in vectorization/load presets.

## Supabase Repository Files

Tracked Supabase project files live under `supabase/`.

```text
supabase/
  migrations/  # SQL schema migrations
  audit/       # read-only DB audit scripts
```

Migration files:

```text
supabase/migrations/001_create_rag_documents.sql
supabase/migrations/002_split_rag_documents.sql
supabase/migrations/003_chat_history.sql
supabase/migrations/004_create_rule_rag_tables.sql
supabase/migrations/005_add_document_priority_columns.sql
```

Audit scripts:

```text
supabase/audit/audit_db.py
supabase/audit/audit_db_extra.py
```

## State And Logs

Crawler state files live at the project root and are ignored by Git:

```text
state.json
state_pknu_notice.json
state_pknu_student_life.json
```

Logs are written under `logs/` and are ignored except `logs/.gitkeep`.

Common log files:

```text
logs/crawler.log
logs/rule_crawler.log
logs/rule_preprocessing.log
logs/main_notice_crawler.log
logs/main_student_life_crawler.log
logs/daily_pipeline.log
```

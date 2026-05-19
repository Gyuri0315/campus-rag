# Data Directory Structure

The project stores crawled and generated RAG data under `files/`.

```text
files/
  ce/
    output/
      json/        # Computer Engineering crawler page JSON
      html/        # Computer Engineering crawler raw HTML
      files/       # Computer Engineering downloaded attachments
    preprocessed/  # Preprocessed chunks from CE attachments
    vectorized/    # CE embeddings, index.jsonl, manifest.json

  rule/
    output/
      json/        # Rule crawler page JSON
      html/        # Rule crawler raw HTML
      files/       # Rule crawler downloaded attachments
    preprocessed/  # Preprocessed chunks from rule attachments
    vectorized/    # Rule embeddings, index.jsonl, manifest.json
```

Default pipeline paths target `files/ce` to preserve the existing Computer Engineering workflow.

Rule crawling uses `files/rule` by default:

```powershell
python scripts/rule/crawler.py --laws --bylaws
```

Rule attachments are downloaded by default. Use `--no-download-files` for a metadata-only crawl.

Rule JSON documents include both combined and source-specific text fields:

- `content`: combined text used for downstream preprocessing.
- `html_text`: text extracted from the crawled HTML body/detail area.
- `page_content`: text visible in the detail page body.
- `preview_content`: preview HTML text when the site exposes it separately.
- `attachment_texts`: extracted text from downloaded attachments, with extraction errors kept per file.

To preprocess and vectorize all rule artifacts (`json`, `html`, and downloaded `files`):

```powershell
python scripts/rule/preprocessing.py
python scripts/rag/vectorization.py --dataset rule --backend sentence-transformers
python scripts/rag/load_to_supabase.py --dataset rule
```

`scripts/rule/preprocessing.py` writes separate preprocessed trees under `files/rule/preprocessed/json`, `files/rule/preprocessed/html`, and `files/rule/preprocessed/file`. Duplicate text is allowed; every chunk keeps `source_kind` metadata so JSON, HTML, and attachment-derived records can still be distinguished.

To preprocess only one artifact type:

```powershell
python scripts/rule/preprocessing.py --source-scope json
python scripts/rule/preprocessing.py --source-scope html
python scripts/rule/preprocessing.py --source-scope files
```

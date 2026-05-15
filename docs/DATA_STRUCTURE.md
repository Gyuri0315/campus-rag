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
python rule_crawler.py --laws --bylaws
```

Rule attachments are downloaded by default. Use `--no-download-files` for a metadata-only crawl.

Rule JSON documents include both combined and source-specific text fields:

- `content`: combined text used for downstream preprocessing.
- `page_content`: text visible in the detail page body.
- `preview_content`: preview HTML text when the site exposes it separately.
- `attachment_texts`: extracted text from downloaded attachments, with extraction errors kept per file.

To preprocess and vectorize rule attachments separately:

```powershell
python preprocessing.py --input-root files/rule/output/files --output-root files/rule/preprocessed --output-json-root files/rule/output/json
python vectorization.py --input-root files/rule/preprocessed --output-root files/rule/vectorized --backend sentence-transformers
python load_to_supabase.py --index-path files/rule/vectorized/index.jsonl
```

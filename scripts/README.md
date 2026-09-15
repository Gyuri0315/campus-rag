# Scripts layout

`scripts` is organized by responsibility. New code should import and execute the canonical paths below.

```text
scripts/
├─ crawlers/
│  ├─ common/
│  │  ├─ schema.py       # document, attachment, stats, and run-result contracts
│  │  ├─ reader.py       # schema-version reader and legacy fallback
│  │  ├─ storage.py      # dataset paths and atomic state persistence
│  │  └─ logging.py      # console and JSONL structured logging
│  ├─ departments/
│  │  ├─ config.py       # DepartmentConfig, SectionConfig, registry validation
│  │  ├─ engine.py       # shared PKNU department CMS parser/downloader
│  │  ├─ sites.csv       # source catalog of colleges, departments, majors, and homepages
│  │  └─ registry.json   # crawl-ready datasets and verified sections
│  ├─ ce.py              # CE entry point backed by the shared engine
│  ├─ pknu_notice.py
│  ├─ pknu_student_life.py
│  └─ pknu_rule.py
├─ migrations/
│  ├─ crawler_documents.py
│  └─ crawler_storage.py
├─ extractors/           # attachment/file format extractors
├─ rag/                  # preprocessing, vectorization, loading, and search
├─ ce/                   # CE preprocessing and priority commands
├─ main/                 # main-site analysis and priority commands
├─ rule/                 # rule preprocessing and priority commands
└─ tests/
```

Canonical crawler commands:

```powershell
python scripts/crawlers/departments/engine.py --dataset ce --once
python scripts/crawlers/pknu_notice.py --recent-only 1
python scripts/crawlers/pknu_student_life.py --mode guide --limit 1
python scripts/crawlers/pknu_rule.py --laws --max-law-items 1
```

The former crawler and root-level common-module paths have been removed. Internal code, jobs, and documentation must use the canonical paths above.

`sites.csv` is the address catalog and may contain sites that are not yet crawler-compatible. `registry.json` contains only configurations with verified menu paths and board IDs. A registry entry links back to the source catalog through `source_catalog_key`.

Department catalog and discovery commands:

```powershell
python scripts/crawlers/departments/cli.py validate
python scripts/crawlers/departments/cli.py probe --site-key ce.pknu.ac.kr
python scripts/crawlers/departments/cli.py discover --site-key ce.pknu.ac.kr
python scripts/crawlers/departments/cli.py discover --dataset ce
```

`probe` performs a shallow homepage fingerprint check. `discover` first requires a compatible probe, then inspects same-host numeric menu pages and writes candidates under `files/_discovery/<site_key>/`. Discovery output is never merged into `registry.json` automatically.

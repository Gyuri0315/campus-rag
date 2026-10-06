# Main website collection

Run commands from the repository root. `pages.json` lists the verified main-site
page IDs, handlers, category paths, link-following rules and attachment options.
`routes.py` validates that configuration; `paths.py` defines the storage layout.
Pages excluded from collection remain in the configuration with `enabled: false`.

## Storage

All main-website results live under `files/pknu_main/`:

```text
files/pknu_main/
  대학소개/
    조직도/
  대학생활/
    학사안내/                 # includes 학사일정, 전공제도, 학점교류
    학사정보/                 # includes E-하나로 and 등록금_안내
    교육과정/                 # includes 이수_로드맵
    수강신청/
    학생생활/                 # includes 교내_식당_주간식단표, 대학생활_가이드
  커뮤니티/
    공지사항/                 # subfolders for each notice category
    부경투데이/
    교수동정/
    부경나우/
  _runs/                      # run results and collection manifests
  _state/                     # incremental crawler state and backups
  _derived/<dataset>/
    preprocessed/             # RAG extraction and chunks
    vectorized/               # embeddings and indexes
  _archive/<dataset>/         # retired, deleted and interrupted results
  _discovery/                 # inventory and page compatibility evidence
```

Each category uses the same format folders, created when needed:

```text
<category>/
  json/
    pages/                    # page body, calendar and collection summaries
    posts/                    # individual board/news articles
    attachments/              # extracted PDF, guide and eBook documents
  html/                       # saved source HTML
  files/<document-id>/
    images/
    pdf/
    office/                   # HWP/HWPX, Word, Excel and PowerPoint
    archives/
    other/
```

Existing hashed filenames and document IDs are preserved. The logical datasets
`pknu_student_life` and `pknu_notice` are also preserved for state, RAG processing
and database identity; their physical folders now share `pknu_main`.
The two incremental state JSON files are kept in Git; other collected data and
state backups remain ignored.
JSON `saved_path`, `document_json` and other local references point to the new
locations. Specialty summaries retain their `main_<id>.json` names.

The common static parser preserves paragraphs, lists, tables with merged cells,
and CMS process diagrams. Image OCR remains reviewable in the JSON; it does not
replace verified HTML text. PDF/eBook documents retain cleaned text by page.

## Commands

```powershell
python -m scripts.main.run --list
python -m scripts.main.run --page-ids 31 92 95 --dry-run
python -m scripts.main.run --page-ids 31 --year 2026
python -m scripts.main.run --page-ids 94
python -m scripts.main.run --page-ids 102
python -m scripts.main.run --page-ids 106 362
python -m scripts.main.run --page-ids 233 234 235
python -m scripts.main.run --page-ids 399 --board-pages 1
python -m scripts.main.run --page-ids 51 52 53
```

`--all` sends live requests for all enabled routes. The default board scope is
one list page. Pages 51, 52 and 53 always collect only the first list page.
Their article JSON includes image/link URLs; image binaries are not downloaded.
Run reports are saved in `_runs/main_run_report.json` and
`_runs/student_life_route_inventory.json`. News manifests live in `_runs/pknu_today/`.

Page 94 includes the graduation-requirements PDF at page 238. Page 102 includes
the linked tuition FAQ board at page 250; the tuition payment page 251 is not
crawled. Curriculum pages 106 and 362 collect their PDF attachments only.
Registered link exclusions (including pages 262, 263, 306–308 and 494) are retained.
Pages 100, 416 and 472 are excluded.

To move a legacy checkout's data, first preview and then apply:

```powershell
python -m scripts.main.migrate_storage
python -m scripts.main.migrate_storage --apply
```

The migration validates destination collisions before moving files, verifies
file hashes, rewrites local JSON/state/RAG references, and checks that previously
valid file references still exist. Interrupted and retired results are archived.
The path mapping and summary are saved in `_runs/storage_migration_paths.json`
and `_runs/storage_migration.json`. Repeating the migration finds no legacy files
to move. This replaces the former academic-info and weekly-menu migration scripts.

## Adding collection scope

Add a verified page to `pages.json` with its existing handler and category path.
If its structure requires another parser, implement that handler under
`collectors/` and dispatch it from `run.py`. Discovery results in `_discovery/`
are evidence for review, not automatic crawl targets. Reusing an existing parser
does not require a new test file; follow `scripts/AGENTS.md` for regression tests.

```text
scripts/main/
  pages.json                  # collection configuration
  routes.py                   # validated routes and compatibility constants
  paths.py                    # category and file-format storage paths
  run.py                      # registered-route entry point
  collectors/                 # calendar API, OCR, tuition, organization, PDFs, news
  discovery/                  # inventory, catalog and coverage inspection
  migrate_storage.py          # legacy data relocation and path rewriting
  student_life_stats.py        # saved document diagnostics
  update_priorities.py        # RAG priority maintenance
```

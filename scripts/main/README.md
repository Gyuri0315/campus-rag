# Main website page collection

`scripts/main` keeps the verified `/main/<id>` routes separate from the broad
page inventory. Run commands from the repository root.

```text
scripts/main/
  routes.py                 # one registry of page IDs and collection handlers
  run.py                    # one entry point for registered routes
  collectors/               # distinct sources: calendar API, image OCR, tuition, organization
  discovery/                # inventory, catalog, coverage, content-type inspection
  student_life_stats.py     # existing output diagnostics
  update_priorities.py      # existing RAG priority maintenance job
```

The existing student-life static crawler now uses
`scripts/crawlers/common/main_static.py` to preserve paragraphs, list items,
table cells and spans, and both CMS process diagram layouts. It handles
`/main/92`, `/main/94`, and `/main/230`–`232`. A new page with the same
structure needs only a reviewed ID in `routes.py`.

The same parser collects grade-management pages `/main/242`–`244` and
lecture-evaluation pages `/main/245`–`246`. It keeps tables nested inside
list items as structured grids. The external "조회하기" portal link on
`/main/246` remains a link in the page content and is not crawled.

`/main/233`–`235` are image-only major-program guides. Their collector saves
the original body image and reviewable Korean OCR blocks. OCR results remain
`needs_review` because they can misread policy wording and portal paths.

The static crawler follows tabs that point to registered file routes. Running
`/main/94` also collects its common graduation-requirements PDF at `/main/238`
through the existing PDF file crawler. Unknown IDs remain `needs_review` and
are not fetched by the generic static parser.

`/main/95` combines a fixed credit-transfer guide with a `bbsId=307` board.
The shared static parser saves the guide separately from the posts. The board
collector follows detail links on the latest list page, saves each post, and
downloads its attachments. Use `--board-pages N` to include more list pages.

```powershell
python -m scripts.main.run --list
python -m scripts.main.run --page-ids 31 92 95 --dry-run
python -m scripts.main.run --page-ids 31 --year 2026
python -m scripts.main.run --page-ids 92 230 231 232
python -m scripts.main.run --page-ids 233 234 235
python -m scripts.main.run --page-ids 94
python -m scripts.main.run --page-ids 242 243 244
python -m scripts.main.run --page-ids 245 246
python -m scripts.main.run --page-ids 95
python -m scripts.main.run --all
```

`--all` makes live requests for every registered page. The run report is saved
under `files/pknu_main/output/main_run_report.json`. The existing crawler's
per-route report is saved beside it as `student_life_route_inventory.json`. Calendar
data is saved under `files/pknu_main/output/academic_calendar/`. Structured
static pages and linked PDFs are saved under `files/pknu_student_life/output/`.
Major-program OCR results are saved under `files/pknu_main/output/major_program/`,
with original images under `files/pknu_main/output/images/`.

Add a new page to `routes.py` only after verifying that its content structure
matches an existing handler. If it needs a separate request or parser, add a
collector under `collectors/` and dispatch it from `run.py`. Inventory results
in `files/_discovery/pknu_main/` are evidence for that review, not an automatic
list of crawl targets.

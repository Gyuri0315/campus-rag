# Main website page collection

`scripts/main` keeps the verified `/main/<id>` routes separate from the broad
page inventory. Run commands from the repository root.

The current `대학생활 > 학사정보` results are stored separately from older
`학사안내_페이지` documents:

- `files/pknu_student_life/output/json/학사정보/`: E-하나로, `/main/101`,
  `/main/104`, `/main/247`, and future static pages until a new site category
  is specified.
- `files/pknu_student_life/output/files/학사정보/`: original images and files
  referenced by those documents.
- `files/pknu_student_life/output/학사정보/등록금_안내/`: the specialized `/main/102`
  tuition data and its route report. `main_102.json` also contains `faq.items[]`
  from the linked `/main/250` board, including attachment text. Each FAQ post
  is saved as a separate document under `output/json/학사정보/`. The
  "등록금납부" tab at `/main/251` is not crawled.

Existing academic guidance pages stay in `output/json/학사안내_페이지/`.

The `대학생활 > 교육과정` category has separate folders:

- `files/pknu_student_life/output/교육과정/main_106.json` and `main_362.json`
  list the downloaded PDF attachments, extraction counts, and short text
  previews. Each item links to its full document JSON.
- `files/pknu_student_life/output/json/교육과정/` stores one document per PDF,
  with cleaned text grouped by page. `pages[]` records whether each page came
  from the PDF text layer or Korean/English OCR, alongside the raw OCR text.
- `files/pknu_student_life/output/files/교육과정/` stores the original PDFs.

The collector reads only PDF attachment links on `/main/106` and `/main/362`.
Their eBook and external service links are not followed. `/main/472` is
explicitly excluded from the route registry.
The two current `/main/362` PDFs contain page images but no text layer. The
collector runs OCR on those pages and marks the results `needs_review` because
small labels and multi-column layouts can be misread. The original PDFs remain
available beside the JSON. Pages with no visible content are marked blank.
Run `python -m scripts.main.migrate_academic_info --dry-run` to inspect the
one-time move of existing results.

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

`/main/101` contains two HTML tables and two image-based workflows. The shared
static crawler saves the original workflow images beside the page JSON and
stores tentative OCR in `images[].ocr_blocks`; the verified HTML text stays in
`content`. The `rise.pknu.ac.kr` reference is not followed.

`/main/247` (student record corrections) and `/main/248` (personal information
changes) also use the shared static parser. Page 247 includes a process diagram
and a table; page 248 contains prose and a portal link.

`/main/306`–`308` are scholarship guidance pages. The shared static parser
preserves their paragraphs and tables under `output/json/학사정보/`. Links within
these pages are not followed by the page collector.

`/main/233`–`235` are image-only major-program guides. Their collector saves
the original body image and reviewable Korean OCR blocks. OCR results remain
`needs_review` because they can misread policy wording and portal paths.

The static crawler follows tabs that point to registered file routes. Running
`/main/94` also collects its common graduation-requirements PDF at `/main/238`
through the existing PDF file crawler. Unknown IDs remain `needs_review` and
are not fetched by the generic static parser.

`/main/100` is excluded from the route registry because it only links to
another site and has no page body to collect.

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
python -m scripts.main.run --page-ids 101
python -m scripts.main.run --page-ids 102
python -m scripts.main.run --page-ids 242 243 244
python -m scripts.main.run --page-ids 245 246
python -m scripts.main.run --page-ids 247 248
python -m scripts.main.run --page-ids 306 307 308
python -m scripts.main.run --page-ids 106 362
python -m scripts.main.run --page-ids 114
python -m scripts.main.run --page-ids 115
python -m scripts.main.run --page-ids 449
python -m scripts.main.run --page-ids 257 258
python -m scripts.main.run --page-ids 118 259 260
python -m scripts.main.run --page-ids 438 494
python -m scripts.main.run --page-ids 263 264
python -m scripts.main.run --page-ids 399 --board-pages 1
python -m scripts.main.run --page-ids 95
python -m scripts.main.run --all
```

`--all` makes live requests for every registered page. The run report is saved
under `files/pknu_main/output/main_run_report.json`. The existing crawler's
per-route report is saved beside it as `student_life_route_inventory.json`. Calendar
data is saved under `files/pknu_main/output/academic_calendar/`. Structured
static pages and linked PDFs are saved under `files/pknu_student_life/output/`.
`/main/399` board posts are stored separately in
`files/pknu_student_life/output/json/교내_식당_주간식단표/`. To relocate posts
collected by an older run, use `python -m scripts.main.migrate_weekly_menu`.
The `/main/114` student ID guide is saved under the `학생생활` subcategory,
including its body image and downloadable PPTX/HWP guides. Newly registered
static pages use `학생생활` until the site category changes; existing academic
guidance and academic information routes keep their established folders.
The `/main/449` certificate guide resolves its two CMS PDF viewer IDs, saves
the PDFs with extracted text, and marks image-heavy PDFs for visual review.
Major-program OCR results are saved under `files/pknu_main/output/major_program/`,
with original images under `files/pknu_main/output/images/`.

The superseded one-off outputs for `/main/92`, `/main/94`, and
`/main/230`–`232` are archived outside the active output tree at
`files/pknu_main/_retired_duplicates_20260930/`. The September-only 2026
calendar snapshot is archived there too; its events are present in the
full-year calendar output. The current static documents and graduation PDF are
under `files/pknu_student_life/output/`.

Add a new page to `routes.py` only after verifying that its content structure
matches an existing handler. If it needs a separate request or parser, add a
collector under `collectors/` and dispatch it from `run.py`. Inventory results
in `files/_discovery/pknu_main/` are evidence for that review, not an automatic
list of crawl targets.

# Test layout

Tests are grouped by the feature they verify:

| Directory | Purpose |
| --- | --- |
| `core/` | Shared crawler schema, storage, logging, and script layout |
| `departments/` | Department discovery, adapters, registry, and transport |
| `main/` | Main-site routes, collectors, and student-life pages |
| `media/` | Body images, OCR, layout classification, and review |
| `fixtures/` | Shared offline inputs for department tests |

`_paths.py` defines the repository root and fixture directory once, so test paths stay valid when modules move.

## When to add a test

Extend an existing parser or collector test when a new HTML shape, failure mode, or data-loss regression needs protection. A new page ID or department using an existing parser is covered by configuration validation and a sample crawl review; it does not need a new test file. Avoid assertions against the current number or order of registered pages and sections.

Reusable validation belongs in crawler configuration, preflight, and parser code. Test-only mock responses and fixtures stay here. The rules for future changes are also recorded in `scripts/AGENTS.md`.

From the repository root:

```powershell
python -m unittest discover -s scripts/tests -t . -p "test_*.py"
```

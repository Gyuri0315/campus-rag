"""Replay failed detail documents from a run receipt without changing state.

Usage: python -m scripts.crawlers.departments.retry_failed RUN_JSON
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

from scripts.crawlers.departments import engine
from scripts.crawlers.departments.config import load_registry
from scripts.crawlers.common.schema import RunResult


def retry_run(path: Path) -> RunResult:
    previous = json.loads(path.read_text(encoding="utf-8"))
    config = load_registry()[previous["dataset"]]
    engine.configure_department(config)
    result = RunResult(dataset=config.dataset, mode="incremental")
    engine.set_run_id(engine.log_context, result.run_id)
    diagnostics = engine.CrawlDiagnostics()
    session = engine.build_session()
    # Work on an in-memory copy only. Historical watermarks stay intact.
    state = engine.load_state()
    groups: dict[str, list[dict]] = {}
    for error in previous.get("errors", []):
        if error["code"] not in {"DOCUMENT_PARSE_FAILED", "EMPTY_BODY"}:
            continue
        url = error["url"]
        match = next((s for s in engine.SECTIONS if urlsplit(s["url"]).path == urlsplit(url).path
                      and all(parse_qs(urlsplit(url).query).get(k) == v
                              for k, v in parse_qs(urlsplit(s["url"]).query).items())), None)
        if match is None:
            result.add_error("RETRY_SECTION_UNKNOWN", url, source_id=error.get("source_id"), url=url)
            continue
        groups.setdefault(match["id"], []).append({
            "post_url": url, "source_id": error["source_id"].split(":", 1)[-1],
            "retry_source_id": error["source_id"], "post_no": None, "is_notice": False, "date": "",
        })
    for section_id, items in groups.items():
        section = next(s for s in engine.SECTIONS if s["id"] == section_id)
        diagnostics.sections_selected += 1
        stats, _ = engine.crawl_board(session, section, state, False, diagnostics=diagnostics, retry_items=items)
        result.stats.add(stats)
    for error in diagnostics.errors:
        result.add_error(**error)
    engine.finish_run_result(result, diagnostics)
    result.save(engine.PROJECT_ROOT)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    args = parser.parse_args()
    result = retry_run(args.run)
    print(json.dumps(result.to_dict(), ensure_ascii=False))
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())

'Main website collection routed by navigation category.'

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from scripts.main.routes import ROUTES, plan
from scripts.main.paths import main_root
from scripts.main.categories.registry import SITE_CATEGORIES, category_executor
from scripts.main.common.storage import write_json as _write_json

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REPORT = main_root(ROOT) / '_runs' / 'main_run_report.json'

def _inside_repo(path: Path) -> Path:
    resolved = path.resolve()
    resolved.relative_to(ROOT)
    return resolved


def execute(page_ids: list[int], *, year: int, months: list[int], full_resync: bool,
            board_pages: int, report_path: Path) -> dict:
    routes = plan(page_ids)
    report = dict(generated_at=datetime.now(timezone.utc).isoformat(),
                  requested_page_ids=list(dict.fromkeys(page_ids)), year=year,
                  routes=routes, collectors={})
    grouped = defaultdict(list)
    for item in routes:
        if item['status'] == 'configured':
            grouped[item['category_path'][0]].append(item)
    for category, items in grouped.items():
        try:
            collectors = category_executor(category)(
                items, category=category, year=year, months=months,
                full_resync=full_resync, board_pages=board_pages)
            report['collectors'][category] = collectors
        except Exception as exc:
            for item in items:
                item.update(status='failed', error=f'{type(exc).__name__}: {exc}')
            report['collectors'][category] = dict(status='failed', error=items[0]['error'])
    statuses = {item['status'] for item in routes}
    report['status'] = ('failed' if 'failed' in statuses else 'needs_review'
                        if statuses & {'needs_review', 'partial_success'} else 'completed')
    _write_json(report_path, report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--page-ids", nargs="+", type=int, metavar="ID")
    selection.add_argument("--all", action="store_true", help="Run every registered page")
    selection.add_argument("--list", action="store_true", help="List registered routes")
    selection.add_argument('--category', choices=tuple(SITE_CATEGORIES),
                           help='Collect registered pages in one navigation category')
    parser.add_argument("--dry-run", action="store_true", help="Show routing without requests or writes")
    parser.add_argument("--year", type=int, default=datetime.now(ZoneInfo("Asia/Seoul")).year)
    parser.add_argument("--months", nargs="+", type=int, metavar="MONTH")
    parser.add_argument("--board-pages", type=int, default=1,
                        help="Limit board list pages (default: 1)")
    parser.add_argument("--full-resync", action="store_true")
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()
    if args.year < 2000 or args.year > 2100 or args.board_pages < 1:
        parser.error("year must be 2000-2100 and board-pages must be >= 1")
    months = args.months or list(range(1, 13))
    if any(not 1 <= month <= 12 for month in months):
        parser.error("months must be 1-12")
    if args.category:
        directory = SITE_CATEGORIES[args.category]['directory']
        page_ids = sorted(i for i, route in ROUTES.items() if route.category_path[0] == directory)
    else:
        page_ids = sorted(ROUTES) if args.all or args.list else args.page_ids
    if args.months and 31 not in page_ids:
        parser.error("--months requires page 31")
    if args.list or args.dry_run:
        print(json.dumps({"routes": plan(page_ids)}, ensure_ascii=False, indent=2))
        return 0
    try:
        report_path = _inside_repo(args.report)
    except ValueError:
        parser.error("report must be inside this repository")
    report = execute(page_ids, year=args.year, months=months,
                     full_resync=args.full_resync, board_pages=args.board_pages,
                     report_path=report_path)
    print(json.dumps({"report": report_path.relative_to(ROOT).as_posix(),
                      "status": report["status"], "routes": report["routes"]},
                     ensure_ascii=False))
    return int(report["status"] == "failed")


if __name__ == '__main__':
    raise SystemExit(main())

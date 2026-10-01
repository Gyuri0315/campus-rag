"""Run only verified /main/<id> routes through their registered collector.

Examples:
    python -m scripts.main.run --list
    python -m scripts.main.run --page-ids 31 92 95 --dry-run
    python -m scripts.main.run --page-ids 31 --year 2026
    python -m scripts.main.run --all
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from scripts.main.routes import ROUTES, STUDENT_LIFE_PAGE_IDS, plan


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REPORT = ROOT / "files/pknu_main/output/main_run_report.json"
STUDENT_LIFE_ROUTE_REPORT = ROOT / "files/pknu_main/output/student_life_route_inventory.json"


def _inside_repo(path: Path) -> Path:
    resolved = path.resolve()
    resolved.relative_to(ROOT)
    return resolved


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _calendar_path(year: int, months: list[int]) -> Path:
    suffix = "" if set(months) == set(range(1, 13)) else "_m" + "-".join(
        f"{month:02d}" for month in sorted(set(months)))
    return ROOT / "files/pknu_main/output/academic_calendar" / f"main_31_{year}{suffix}.json"


def execute(page_ids: list[int], *, year: int, months: list[int], full_resync: bool,
            board_pages: int, report_path: Path) -> dict:
    """Collect configured routes; leave unknown IDs as explicit review items."""
    from scripts.crawlers import pknu_student_life
    from scripts.main.collectors.academic_calendar import collect_calendar
    from scripts.main.collectors.major_program import collect_major_program

    routes = plan(page_ids)
    report = {"generated_at": datetime.now(timezone.utc).isoformat(),
              "requested_page_ids": list(dict.fromkeys(page_ids)), "year": year,
              "routes": routes, "collectors": {}}
    student_life_ids = [item["page_id"] for item in routes
                        if item["page_id"] in STUDENT_LIFE_PAGE_IDS]
    if student_life_ids:
        try:
            stats = pknu_student_life.run(
                "pages", full_resync, None, page_ids=tuple(student_life_ids),
                board_pages=board_pages, route_report=STUDENT_LIFE_ROUTE_REPORT)
            details = json.loads(STUDENT_LIFE_ROUTE_REPORT.read_text(encoding="utf-8"))
            by_id = {row["page_id"]: row for row in details["routes"]}
            for item in routes:
                if item["page_id"] in student_life_ids:
                    detail = by_id.get(item["page_id"])
                    item["status"] = detail["status"] if detail else "executed"
                    if detail:
                        item["result"] = detail
            report["collectors"]["student_life_pages"] = {
                "status": "failed" if stats.failed else "completed",
                "stats": stats.to_dict(),
                "report_path": STUDENT_LIFE_ROUTE_REPORT.relative_to(ROOT).as_posix()}
        except Exception as exc:
            report["collectors"]["student_life_pages"] = {
                "status": "failed", "error": f"{type(exc).__name__}: {exc}"}
            for item in routes:
                if item["page_id"] in student_life_ids:
                    item["status"] = "failed"
    if any(item["handler"] == "notice" for item in routes):
        from scripts.crawlers import pknu_notice
        item = next(item for item in routes if item["handler"] == "notice")
        try:
            stats = pknu_notice.run_crawl(full_resync=full_resync,
                                          recent_only=None, only_cd=None)
            item.update(status="failed" if stats.failed else "completed",
                        stats=stats.to_dict())
            report["collectors"]["notice"] = {"status": item["status"],
                                               "stats": item["stats"]}
        except Exception as exc:
            item.update(status="failed", error=f"{type(exc).__name__}: {exc}")
            report["collectors"]["notice"] = {"status": "failed", "error": item["error"]}
    if any(item["handler"] == "guide" for item in routes):
        item = next(item for item in routes if item["handler"] == "guide")
        try:
            stats = pknu_student_life.run("guide", full_resync, None)
            item.update(status="failed" if stats.failed else "completed",
                        stats=stats.to_dict())
            report["collectors"]["guide"] = {"status": item["status"],
                                              "stats": item["stats"]}
        except Exception as exc:
            item.update(status="failed", error=f"{type(exc).__name__}: {exc}")
            report["collectors"]["guide"] = {"status": "failed", "error": item["error"]}
    if any(item["handler"] == "academic_calendar" for item in routes):
        item = next(item for item in routes if item["page_id"] == 31)
        try:
            with pknu_student_life.build_session(item["url"]) as session:
                data = collect_calendar(session, year, months)
            target = _calendar_path(year, months)
            _write_json(target, data)
            item.update(status=data["status"], output=target.relative_to(ROOT).as_posix(),
                        event_count=data["event_count"],
                        rejected_count=data["rejected_count"])
            report["collectors"]["academic_calendar"] = {
                "status": data["status"], "output": item["output"]}
        except Exception as exc:
            item.update(status="failed", error=f"{type(exc).__name__}: {exc}")
            report["collectors"]["academic_calendar"] = {"status": "failed",
                                                         "error": item["error"]}
    major_items = [item for item in routes if item["handler"] == "major_program"]
    if major_items:
        with pknu_student_life.build_session(major_items[0]["url"]) as session:
            for item in major_items:
                try:
                    data = collect_major_program(session, item["page_id"])
                    target = (ROOT / "files/pknu_main/output/major_program"
                              / f'main_{item["page_id"]}.json')
                    _write_json(target, data)
                    item.update(status=data["status"],
                                output=target.relative_to(ROOT).as_posix(),
                                image_count=data["image_count"],
                                ocr_block_count=data["ocr_block_count"])
                except Exception as exc:
                    item.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        report["collectors"]["major_program"] = {
            "status": "failed" if any(item["status"] == "failed" for item in major_items)
            else "needs_review", "page_ids": [item["page_id"] for item in major_items]}
    curriculum_items = [item for item in routes if item["handler"] == "curriculum_files"]
    if curriculum_items:
        from scripts.main.collectors.curriculum_files import collect_curriculum_files

        with pknu_student_life.build_session(curriculum_items[0]["url"]) as session:
            for item in curriculum_items:
                try:
                    data = collect_curriculum_files(
                        session, item["page_id"], full_resync=full_resync,
                    )
                    item.update(status=data["status"], output=data["output"],
                                attachment_count=data["attachment_count"],
                                attachments=[{key: value for key, value in attachment.items()
                                              if key != "text_preview"}
                                             for attachment in data["attachments"]])
                except Exception as exc:
                    item.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        report["collectors"]["curriculum_files"] = {
            "status": "failed" if any(item["status"] == "failed" for item in curriculum_items)
            else "needs_review" if any(item["status"] == "needs_review" for item in curriculum_items)
            else "completed", "page_ids": [item["page_id"] for item in curriculum_items]}
    report["status"] = ("failed" if any(item["status"] == "failed" for item in routes)
                        or any(item["status"] == "failed" for item in report["collectors"].values())
                        else "needs_review" if any(item["status"] == "needs_review" for item in routes)
                        else "completed")
    _write_json(report_path, report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--page-ids", nargs="+", type=int, metavar="ID")
    selection.add_argument("--all", action="store_true", help="Run every registered page")
    selection.add_argument("--list", action="store_true", help="List registered routes")
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


if __name__ == "__main__":
    raise SystemExit(main())

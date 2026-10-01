"""Move existing /main/399 posts into their dedicated weekly-menu folder."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from scripts.crawlers.common.storage import save_state_atomic


ROOT = Path(__file__).resolve().parents[2]
DATASET_ROOT = ROOT / "files/pknu_student_life"
OUTPUT = DATASET_ROOT / "output"
OLD_CATEGORY = "학생생활"
NEW_CATEGORY = "교내_식당_주간식단표"
OLD_JSON = OUTPUT / "json" / OLD_CATEGORY
NEW_JSON = OUTPUT / "json" / NEW_CATEGORY
OLD_ASSETS = OUTPUT / "files" / OLD_CATEGORY
NEW_ASSETS = OUTPUT / "files" / NEW_CATEGORY
OLD_JSON_PREFIX = f"files/pknu_student_life/output/json/{OLD_CATEGORY}/"
NEW_JSON_PREFIX = f"files/pknu_student_life/output/json/{NEW_CATEGORY}/"
OLD_ASSET_PREFIX = f"files/pknu_student_life/output/files/{OLD_CATEGORY}/"
NEW_ASSET_PREFIX = f"files/pknu_student_life/output/files/{NEW_CATEGORY}/"


def _inside_output(path: Path) -> None:
    path.resolve().relative_to(OUTPUT.resolve())


def _write_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n",
                         encoding="utf-8")
    temporary.replace(path)


def _rewrite_report_paths(value: object, moved_slugs: set[str]) -> bool:
    changed = False
    if isinstance(value, dict):
        for key, item in value.items():
            if (key == "output" and isinstance(item, str)
                    and item.startswith(OLD_JSON_PREFIX)
                    and Path(item).stem in moved_slugs):
                value[key] = NEW_JSON_PREFIX + Path(item).name
                changed = True
            else:
                changed |= _rewrite_report_paths(item, moved_slugs)
    elif isinstance(value, list):
        for item in value:
            changed |= _rewrite_report_paths(item, moved_slugs)
    return changed


def migrate(*, dry_run: bool = False) -> dict:
    state_path = DATASET_ROOT / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    documents = []
    for source in OLD_JSON.glob("*.json"):
        doc = json.loads(source.read_text(encoding="utf-8"))
        if doc.get("metadata", {}).get("page_id") != 399:
            continue
        slug = source.stem
        target = NEW_JSON / source.name
        old_assets = OLD_ASSETS / slug
        new_assets = NEW_ASSETS / slug
        for path in (source, target, old_assets, new_assets):
            _inside_output(path)
        if target.exists() or (old_assets.exists() and new_assets.exists()):
            raise FileExistsError(f"weekly-menu migration target exists: {target}")
        source_id = f"main:399:{doc['metadata']['post_no']}"
        if state.get("items", {}).get(source_id, {}).get("slug") != slug:
            raise ValueError(f"state entry does not match {source_id}")
        documents.append((source, target, old_assets, new_assets, source_id, doc))

    moved_slugs = {source.stem for source, *_ in documents}
    if not dry_run and documents:
        NEW_JSON.mkdir(parents=True, exist_ok=True)
        for source, target, old_assets, new_assets, source_id, doc in documents:
            if old_assets.exists():
                new_assets.parent.mkdir(parents=True, exist_ok=True)
                old_assets.rename(new_assets)
            for attachment in doc.get("attachments", []):
                saved = attachment.get("saved_path")
                if isinstance(saved, str) and saved.startswith(OLD_ASSET_PREFIX):
                    attachment["saved_path"] = NEW_ASSET_PREFIX + saved[len(OLD_ASSET_PREFIX):]
            doc["subcategory"] = NEW_CATEGORY
            source.rename(target)
            _write_json(target, doc)
            state["items"][source_id]["subcategory"] = NEW_CATEGORY
        save_state_atomic(state_path, state, "pknu_student_life")

    report_paths = (
        OUTPUT / "runs/main_399_page1_20261001.json",
        ROOT / "files/pknu_main/output/main_run_report.json",
        ROOT / "files/pknu_main/output/student_life_route_inventory.json",
    )
    updated_reports = []
    for path in report_paths:
        if not path.is_file():
            continue
        report = json.loads(path.read_text(encoding="utf-8"))
        if _rewrite_report_paths(report, moved_slugs):
            updated_reports.append(path.relative_to(ROOT).as_posix())
            if not dry_run:
                _write_json(path, report)
    return {"documents": len(documents), "destination": NEW_JSON.relative_to(ROOT).as_posix(),
            "updated_reports": updated_reports, "dry_run": dry_run}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(json.dumps(migrate(dry_run=args.dry_run), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

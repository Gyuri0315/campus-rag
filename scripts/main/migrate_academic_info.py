"""Move the existing student-life 학사정보 results into their new category."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "files/pknu_student_life/output"
ACADEMIC_INFO = "학사정보"
STATIC_PAGE_IDS = (101, 104, 247)
EBOOK_URL = "https://www.pknu.ac.kr/ebook/col_life/kor/index.html"


def _within_output(path: Path) -> Path:
    resolved = path.resolve()
    resolved.relative_to(OUTPUT.resolve())
    return resolved


def _read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON document is not an object: {path}")
    return value


def _write_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n",
                         encoding="utf-8")
    temporary.replace(path)


def _move(source: Path, target: Path, *, dry_run: bool, actions: list[str]) -> None:
    _within_output(source)
    _within_output(target)
    if source == target:
        return
    if not source.exists():
        return
    if target.exists():
        raise FileExistsError(f"both old and new paths exist: {source}, {target}")
    actions.append(f"move {source.relative_to(ROOT)} -> {target.relative_to(ROOT)}")
    if not dry_run:
        target.parent.mkdir(parents=True, exist_ok=True)
        source.rename(target)


def _relocate_json(source: Path, target: Path, *, dry_run: bool,
                   actions: list[str], image_slug: str | None = None) -> None:
    _move(source, target, dry_run=dry_run, actions=actions)
    path = source if dry_run and source.exists() else target
    if not path.is_file():
        raise FileNotFoundError(f"expected source or destination document: {target}")
    doc = _read_json(path)
    changed = doc.get("subcategory") != ACADEMIC_INFO
    if image_slug:
        old_prefix = f"files/pknu_student_life/output/files/학사안내_페이지/{image_slug}/"
        new_prefix = f"files/pknu_student_life/output/files/{ACADEMIC_INFO}/{image_slug}/"
        for image in doc.get("images", []):
            saved_path = image.get("saved_path")
            if isinstance(saved_path, str) and saved_path.startswith(old_prefix):
                image["saved_path"] = new_prefix + saved_path[len(old_prefix):]
                changed = True
    if changed:
        doc["subcategory"] = ACADEMIC_INFO
        actions.append(f"update {target.relative_to(ROOT)} metadata")
        if not dry_run:
            _write_json(target, doc)


def migrate(*, dry_run: bool = False) -> list[str]:
    actions: list[str] = []
    old_static = OUTPUT / "json/학사안내_페이지"
    new_static = OUTPUT / "json" / ACADEMIC_INFO
    for page_id in STATIC_PAGE_IDS:
        url = f"https://www.pknu.ac.kr/main/{page_id}"
        matches = [path for directory in (old_static, new_static)
                   for path in directory.glob("*.json")
                   if _read_json(path).get("url") == url]
        if len(matches) != 1:
            raise ValueError(f"expected one active result for /main/{page_id}, found {len(matches)}")
        source = matches[0]
        target = new_static / source.name
        slug = source.stem if page_id == 101 else None
        if slug:
            _move(OUTPUT / "files/학사안내_페이지" / slug,
                  OUTPUT / "files" / ACADEMIC_INFO / slug,
                  dry_run=dry_run, actions=actions)
        _relocate_json(source, target, dry_run=dry_run, actions=actions, image_slug=slug)

    old_ebook = OUTPUT / "json/E-하나로"
    matches = [path for directory in (old_ebook, new_static)
               for path in directory.glob("*.json")
               if _read_json(path).get("url") == EBOOK_URL]
    if len(matches) != 1:
        raise ValueError(f"expected one active E-하나로 result, found {len(matches)}")
    _relocate_json(matches[0], new_static / matches[0].name,
                   dry_run=dry_run, actions=actions)

    old_tuition = OUTPUT / "tuition"
    new_tuition = OUTPUT / ACADEMIC_INFO / "등록금_안내"
    data_source = old_tuition / "main_102.json"
    data_target = new_tuition / "main_102.json"
    _move(data_source, data_target, dry_run=dry_run, actions=actions)
    data_path = data_source if dry_run and data_source.is_file() else data_target
    data = _read_json(data_path)
    if (data.get("category") != "대학생활" or data.get("subcategory") != ACADEMIC_INFO
            or data.get("page_url") != "https://www.pknu.ac.kr/main/102"):
        data.update(category="대학생활", subcategory=ACADEMIC_INFO,
                    page_url="https://www.pknu.ac.kr/main/102")
        actions.append(f"update {data_target.relative_to(ROOT)} metadata")
        if not dry_run:
            _write_json(data_target, data)

    route_source = old_tuition / "main_102_route.json"
    route_target = new_tuition / "main_102_route.json"
    _move(route_source, route_target, dry_run=dry_run, actions=actions)
    route_path = route_source if dry_run and route_source.is_file() else route_target
    if route_path.is_file():
        route = _read_json(route_path)
        expected = data_target.relative_to(ROOT).as_posix()
        changed = False
        for item in route.get("routes", []):
            if item.get("page_id") == 102 and item.get("data_path") != expected:
                item["data_path"] = expected
                changed = True
        if changed:
            actions.append(f"update {route_target.relative_to(ROOT)} data_path")
            if not dry_run:
                _write_json(route_target, route)

    if not dry_run:
        for directory in (old_ebook, old_tuition, OUTPUT / "files/학사안내_페이지"):
            if directory.is_dir() and not any(directory.iterdir()):
                _within_output(directory)
                directory.rmdir()
    return actions


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    for action in migrate(dry_run=args.dry_run):
        print(action)


if __name__ == "__main__":
    main()

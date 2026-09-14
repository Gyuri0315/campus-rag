"""Repair mojibake placeholder categories in the department registry.

The command is a dry-run unless ``--apply`` is supplied.  It only changes the
``category`` field of sections whose current value is exactly ``????``.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REGISTRY = PROJECT_ROOT / "scripts" / "crawlers" / "departments" / "registry.json"


RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("공지사항", re.compile(r"공지|알림|notice", re.IGNORECASE)),
    ("자료실", re.compile(r"자료|서식|양식|규정|매뉴얼|다운로드|프로그램설치")),
    ("취업정보", re.compile(r"취업|채용|구인|진로|공모|자격|해기사|먼저\s*걸어본\s*길")),
    ("입학안내", re.compile(r"입학|신입생|모집요강|모집공고")),
    ("교육과정", re.compile(r"교육과정|교과과정|교과목|교과목해설|졸업|학사|시간표|수강|장학|학위|로드맵|역량")),
    ("교수진", re.compile(r"교수|교직원|조교")),
    ("연구·산학", re.compile(r"연구|세미나|학술|실험실")),
    ("학생활동", re.compile(
        r"학생회|동아리|소모임|학생활동|대학생활|행사|앨범|갤러리|사진|홍보|작품|추억|"
        r"국제화프로그램|학내프로그램|학외프로그램|현장실습|배움나눔|위버멘시|모의|"
        r"학생실적|박람회|비교과|일문동감|Air-up|HORA|i;Dear|MYTH|LiNK|ACE|LA_ON",
        re.IGNORECASE,
    )),
    ("대학원", re.compile(r"대학원")),
    ("학과안내", re.compile(
        r"소개|안내|연혁|오시는|찾아오시는|contact|조직|인사말|비전|목표|학부|학과|전공|"
        r"발전계획|현황|개요|캠퍼스\s*약도",
        re.IGNORECASE,
    )),
    ("커뮤니티", re.compile(
        r"Q\s*&?\s*A|QnA|FAQ|묻고답|질문|자유게시판|게시판|소식|뉴스|커뮤니티|기타|"
        r"상담|SNS|정보나눔|일반|프로그램|이슈이슈|부경법학",
        re.IGNORECASE,
    )),
)


def infer_category(section: dict[str, Any]) -> tuple[str | None, str | None]:
    name = re.sub(r"\s+", " ", str(section.get("name") or "").strip())
    if not name:
        return None, None
    for category, pattern in RULES:
        if pattern.search(name):
            # Generic board group labels are notice-board parents, not guides.
            if (
                section.get("kind") == "board"
                and category in {"대학원", "학과안내"}
                and re.fullmatch(r"(학과|학부|전공|대학원|일반)", name)
            ):
                return "공지사항", "generic_board_group"
            return category, pattern.pattern
    return None, None


def plan_registry(payload: dict[str, Any]) -> dict[str, Any]:
    changes: list[dict[str, str]] = []
    unresolved: list[dict[str, str]] = []
    for department in payload.get("departments") or []:
        dataset = str(department.get("dataset") or "")
        for section in department.get("sections") or []:
            if section.get("category") != "????":
                continue
            category, reason = infer_category(section)
            item = {
                "dataset": dataset,
                "section_id": str(section.get("id") or ""),
                "name": str(section.get("name") or ""),
            }
            if category is None:
                unresolved.append(item)
                continue
            section["category"] = category
            changes.append({**item, "category": category, "rule": str(reason)})
    return {"changes": changes, "unresolved": unresolved}


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def build_report(payload: dict[str, Any], plan: dict[str, Any], *, applied: bool) -> dict[str, Any]:
    category_counts = Counter(change["category"] for change in plan["changes"])
    return {
        "status": "ready" if not plan["unresolved"] else "pending_review",
        "mode": "apply" if applied else "dry_run",
        "datasets": len({change["dataset"] for change in plan["changes"]}),
        "sections": sum(len(item.get("sections") or []) for item in payload.get("departments") or []),
        "changes": len(plan["changes"]),
        "unresolved": plan["unresolved"],
        "category_counts": dict(sorted(category_counts.items())),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    payload = json.loads(args.registry.read_text(encoding="utf-8"))
    plan = plan_registry(payload)
    report = build_report(payload, plan, applied=args.apply)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if plan["unresolved"]:
        return 1
    if args.apply:
        write_json_atomic(args.registry, payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

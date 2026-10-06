"""Verified main-site collection rules loaded from pages.json."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path


CONFIG_FILE = Path(__file__).with_name("pages.json")
_rows = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))["pages"]
PAGE_SETTINGS: dict[int, dict] = {}
for _row in _rows:
    _id = _row["page_id"]
    if not isinstance(_id, int) or _id in PAGE_SETTINGS:
        raise ValueError(f"invalid or duplicate page ID: {_id}")
    if _row.get("enabled"):
        if not _row.get("handler") or not _row.get("category_path"):
            raise ValueError(f"page {_id} needs a handler and category_path")
        for _part in _row["category_path"]:
            if not isinstance(_part, str) or not _part or _part in {".", ".."} or any(c in _part for c in '/\\:*?"<>|'):
                raise ValueError(f"invalid category component on page {_id}")
    PAGE_SETTINGS[_id] = _row


@dataclass(frozen=True)
class MainRoute:
    page_id: int
    handler: str
    category_path: tuple[str, ...] = ()
    follow_links: bool = True
    download_attachments: bool = True

    @property
    def url(self) -> str:
        return f"https://www.pknu.ac.kr/main/{self.page_id}"


def _ids(handler: str) -> tuple[int, ...]:
    return tuple(i for i, row in PAGE_SETTINGS.items() if row.get("enabled") and row["handler"] == handler)


STATIC_PAGE_IDS = _ids("static")
BOARD_PAGE_IDS = _ids("board")
LINK_HUB_PAGE_IDS = _ids("link_hub")
TUITION_PAGE_IDS = _ids("tuition")
ORG_PAGE_IDS = _ids("organization")
CURRICULUM_FILE_PAGE_IDS = _ids("curriculum_files")
FILE_PAGE_IDS = _ids("file")
ACADEMIC_CALENDAR_PAGE_IDS = _ids("academic_calendar")
MAJOR_PROGRAM_PAGE_IDS = _ids("major_program")
NOTICE_PAGE_IDS = _ids("notice")
TODAY_PAGE_IDS = _ids("today")
GUIDE_PAGE_IDS = _ids("guide")
REDIRECT_PAGE_TARGETS = {i: PAGE_SETTINGS[i]["redirect_target"] for i in _ids("redirect")}
EXCLUDED_PAGE_IDS = frozenset(i for i, row in PAGE_SETTINGS.items() if not row.get("enabled"))
NO_FOLLOW_LINK_PAGE_IDS = frozenset(i for i in STATIC_PAGE_IDS if not PAGE_SETTINGS[i].get("follow_links", True))
ACADEMIC_GUIDE_STATIC_PAGE_IDS = frozenset(i for i in (*STATIC_PAGE_IDS, 95) if PAGE_SETTINGS[i]["category_path"][1] == "학사안내")
ACADEMIC_INFO_STATIC_PAGE_IDS = frozenset(i for i in STATIC_PAGE_IDS if PAGE_SETTINGS[i]["category_path"][1] == "학사정보")
ROUTE_GROUPS = {handler: _ids(handler) for handler in dict.fromkeys(row["handler"] for row in PAGE_SETTINGS.values() if row.get("enabled"))}


def _build_routes() -> dict[int, MainRoute]:
    routes: dict[int, MainRoute] = {}
    for handler, page_ids in ROUTE_GROUPS.items():
        for page_id in page_ids:
            if page_id in routes:
                raise ValueError(f"/main/{page_id} has multiple handlers")
            row = PAGE_SETTINGS.get(page_id, {})
            routes[page_id] = MainRoute(page_id, handler, tuple(row.get("category_path", ())),
                                        row.get("follow_links", True), row.get("download_attachments", True))
    if routes.keys() & EXCLUDED_PAGE_IDS:
        raise ValueError(f"excluded main pages have handlers: {sorted(routes.keys() & EXCLUDED_PAGE_IDS)}")
    if NO_FOLLOW_LINK_PAGE_IDS - set(STATIC_PAGE_IDS):
        raise ValueError("no-follow pages must be static")
    return routes


ROUTES = _build_routes()
STUDENT_LIFE_PAGE_IDS = frozenset(ROUTES.keys() - set(ACADEMIC_CALENDAR_PAGE_IDS)
    - set(MAJOR_PROGRAM_PAGE_IDS) - set(NOTICE_PAGE_IDS) - set(TODAY_PAGE_IDS)
    - set(GUIDE_PAGE_IDS) - set(CURRICULUM_FILE_PAGE_IDS))
CONFIGURED_PAGE_IDS = frozenset(ROUTES)


def plan(page_ids: list[int]) -> list[dict]:
    return [{"page_id": i, "url": f"https://www.pknu.ac.kr/main/{i}",
             "handler": ROUTES[i].handler if i in ROUTES else None,
             "category_path": list(ROUTES[i].category_path) if i in ROUTES else [],
             "status": "configured" if i in ROUTES else "excluded" if i in EXCLUDED_PAGE_IDS else "needs_review"}
            for i in dict.fromkeys(page_ids)]

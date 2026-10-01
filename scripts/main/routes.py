"""Verified /main/<id> routes and their collection strategy.

Only IDs listed here may enter the collection runner. Inventory/catalog labels
are observations, not permission to send an unverified page to a generic parser.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MainRoute:
    page_id: int
    handler: str

    @property
    def url(self) -> str:
        return f"https://www.pknu.ac.kr/main/{self.page_id}"


# Existing, verified pknu_student_life page routes.
STATIC_PAGE_IDS: tuple[int, ...] = (
    17, 92, 93, 94, 96, 97, 98, 99, 101, 103, 104, 114, 115, 117,
    118, 119, 230, 231, 232, 237, 242, 243, 244, 245, 246, 247, 248,
    257, 258, 259, 260, 262, 263, 264, 306, 307, 308, 438, 449, 481, 494,
)
# These pages are collected only at their own URLs. Links inside them remain
# part of the page content and never add another route to the run.
NO_FOLLOW_LINK_PAGE_IDS = frozenset({262, 263, 306, 307, 308, 494})
# Previously collected pages retain their original folders. Newly registered
# static pages default to the current student-life category.
ACADEMIC_GUIDE_STATIC_PAGE_IDS = frozenset({
    17, 92, 93, 94, 96, 97, 98, 99, 103, 117, 119,
    230, 231, 232, 237, 242, 243, 244, 245, 246, 248, 481,
    95,  # fixed introduction above the credit-transfer board
})
ACADEMIC_INFO_STATIC_PAGE_IDS = frozenset({101, 104, 247, 306, 307, 308})
BOARD_PAGE_IDS = (95, 399)
# /main/100 only forwards to another site and has no collectable page body.
# /main/416 (image-only facility map) is deferred from collection.
# /main/472 (custom scholarship search) is explicitly outside the collection scope.
EXCLUDED_PAGE_IDS = frozenset({100, 416, 472})
LINK_HUB_PAGE_IDS = (110,)
TUITION_PAGE_IDS = (102,)
ORG_PAGE_IDS = (533,)
CURRICULUM_FILE_PAGE_IDS = (106, 362)
REDIRECT_PAGE_TARGETS = {
    112: "https://irumi.pknu.ac.kr/link.jsp?menuId=U020913",
    528: "https://yebigun.pknu.ac.kr/",
}
FILE_PAGE_IDS = (238,)
ACADEMIC_CALENDAR_PAGE_IDS = (31,)
MAJOR_PROGRAM_PAGE_IDS = (233, 234, 235)
NOTICE_PAGE_IDS = (163,)
GUIDE_PAGE_IDS = (434,)

ROUTE_GROUPS = {
    "static": STATIC_PAGE_IDS,
    "board": BOARD_PAGE_IDS,
    "link_hub": LINK_HUB_PAGE_IDS,
    "tuition": TUITION_PAGE_IDS,
    "organization": ORG_PAGE_IDS,
    "curriculum_files": CURRICULUM_FILE_PAGE_IDS,
    "redirect": tuple(REDIRECT_PAGE_TARGETS),
    "file": FILE_PAGE_IDS,
    "academic_calendar": ACADEMIC_CALENDAR_PAGE_IDS,
    "major_program": MAJOR_PROGRAM_PAGE_IDS,
    "notice": NOTICE_PAGE_IDS,
    "guide": GUIDE_PAGE_IDS,
}


def _build_routes() -> dict[int, MainRoute]:
    routes: dict[int, MainRoute] = {}
    for handler, page_ids in ROUTE_GROUPS.items():
        for page_id in page_ids:
            if page_id in routes:
                raise ValueError(f"/main/{page_id} has multiple handlers")
            routes[page_id] = MainRoute(page_id, handler)
    excluded_routes = routes.keys() & EXCLUDED_PAGE_IDS
    if excluded_routes:
        raise ValueError(f"excluded main pages have handlers: {sorted(excluded_routes)}")
    invalid_no_follow = NO_FOLLOW_LINK_PAGE_IDS - set(STATIC_PAGE_IDS)
    if invalid_no_follow:
        raise ValueError(f"no-follow pages must be static: {sorted(invalid_no_follow)}")
    return routes


ROUTES = _build_routes()
STUDENT_LIFE_PAGE_IDS = frozenset(
    ROUTES.keys() - set(ACADEMIC_CALENDAR_PAGE_IDS) - set(MAJOR_PROGRAM_PAGE_IDS)
    - set(NOTICE_PAGE_IDS) - set(GUIDE_PAGE_IDS) - set(CURRICULUM_FILE_PAGE_IDS)
)
CONFIGURED_PAGE_IDS = frozenset(ROUTES)


def plan(page_ids: list[int]) -> list[dict]:
    """Describe a requested run, including unknown IDs, without fetching pages."""
    return [{"page_id": page_id, "url": f"https://www.pknu.ac.kr/main/{page_id}",
             "handler": ROUTES[page_id].handler if page_id in ROUTES else None,
             "status": ("configured" if page_id in ROUTES else "excluded"
                        if page_id in EXCLUDED_PAGE_IDS else "needs_review")}
            for page_id in dict.fromkeys(page_ids)]

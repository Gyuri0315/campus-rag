"""Course offerings (강의계획서 조회 목록) from irumi's public lookup screen.

www.pknu.ac.kr/main/112 ("강의계획서 조회") redirects to the Nexacro screen
uni::cour/open/LtPdocInq on irumi.pknu.ac.kr. Its search runs without login
through /ezworks/noSessionController (sqlId LtPdocInq.findLtPdocInq) and
returns one row per class section: course name/number, section, professor,
credits (학점-이론-실습), 이수구분, 수업방식.

Not collected, on purpose:
- the syllabus body (weekly plan, grading): the screen opens it in a report
  viewer whose request payload the site encrypts client-side;
- the college/department dropdown: the site's web firewall blocks that call
  even from the official screen.
So the search is driven by names instead: professors first (from the
department faculty page), then every course name those searches turned up,
which also finds sections taught by lecturers. Requests are sequential and
spaced out (REQUEST_DELAY_SECONDS).

Usage:
    python -m scripts.crawlers.pknu_syllabus --year 2026 --term 2 --out output/syllabus/2026-2.json
"""

from __future__ import annotations

import argparse
import html
import json
import re
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import requests

from scripts.crawlers.common.schema import apply_common_schema

BASE_URL = "https://irumi.pknu.ac.kr"
ENDPOINT = BASE_URL + "/ezworks/noSessionController"
SCREEN_URL = "https://www.pknu.ac.kr/main/112"
SQL_ID = "LtPdocInq.findLtPdocInq"
REQUEST_DELAY_SECONDS = 1.5
# irumi's code for each term ("U0003002" is the 2nd semester; the screen
# itself treats 2020/U0003002 as "2020년 2학기").
TERM_CODES = {"1": "U0003001", "2": "U0003002"}
TERM_LABELS = {"1": "1학기", "2": "2학기"}

# Department names as irumi prints them for 컴퓨터·인공지능공학부 (undergraduate).
DEPARTMENTS = ("컴퓨터공학전공", "인공지능전공", "컴퓨터·인공지능공학부")
# ce.pknu.ac.kr/ce/1805 (교수진), 2026-10-19.
FACULTY = (
    "김영봉", "권오흠", "신봉기", "송하주", "신상욱", "김종남", "한영선", "장원두",
    "최필주", "신인철", "김태국", "김훈희", "유승호", "원지혜", "오상호", "황상원",
    "김기윤", "김재열",
)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (campus-rag course list; PKNU capstone)",
    # The controller rejects requests without the Nexacro client marker.
    "ezclienttype": "nexacro17",
    "X-Requested-With": "XMLHttpRequest",
    "Accept": "application/xml, text/xml, */*",
    "Content-Type": "text/xml",
}

_DATASET_RE = re.compile(r'<Dataset id="([^"]+)">(.*?)</Dataset>', re.S)
_ROW_RE = re.compile(r"<Row[^>]*>(.*?)</Row>", re.S)
_COL_RE = re.compile(r'<Col id="([^"]+)">(.*?)</Col>', re.S)


def build_request(year: int, term: str, *, course_name: str = "", staff_name: str = "") -> str:
    params = {
        "_sqlId": SQL_ID, "_method": "getList",
        "YY": str(year), "SHTM_CD": TERM_CODES[term],
        "UNIV_GRSC_CLSF": "", "COLG_CD": "", "OPEN_SUST_MJ_CD": "",
        "SBJT_NM": course_name, "STAFF_NM": staff_name, "SBJ_FG": "",
    }
    parameters = "".join(
        f'<Parameter id="{key}">{html.escape(value)}</Parameter>' for key, value in params.items()
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Root xmlns="http://www.nexacroplatform.com/platform/dataset">'
        f"<Parameters>{parameters}</Parameters>"
        '<Dataset id="__DS_TRANS_INFO__"><ColumnInfo>'
        '<Column id="strAction" type="string" size="256" />'
        '<Column id="strSvcID" type="string" size="256" />'
        '<Column id="strURL" type="string" size="256" />'
        "</ColumnInfo><Rows><Row>"
        '<Col id="strAction">uni</Col>'
        '<Col id="strSvcID">fn_search[^]1[^]getList</Col>'
        f'<Col id="strURL">{ENDPOINT}</Col>'
        "</Row></Rows></Dataset></Root>"
    )


def parse_rows(xml_text: str, dataset_id: str = "output1") -> List[Dict[str, str]]:
    """Rows of one Nexacro dataset as dicts. Raises on an error response."""
    error = re.search(r'<Parameter id="ErrorCode"[^>]*>(-?\d+)</Parameter>', xml_text)
    if error is None or error.group(1) != "0":
        raise ValueError(f"irumi returned an error or non-dataset response: {xml_text[:200]!r}")
    rows: List[Dict[str, str]] = []
    for name, body in _DATASET_RE.findall(xml_text):
        if name != dataset_id:
            continue
        for row in _ROW_RE.findall(body):
            rows.append({key: html.unescape(value).strip() for key, value in _COL_RE.findall(row)})
    return rows


def _search(session: requests.Session, year: int, term: str, **kwargs: str) -> List[Dict[str, str]]:
    response = session.post(ENDPOINT, data=build_request(year, term, **kwargs).encode("utf-8"), timeout=30)
    response.raise_for_status()
    time.sleep(REQUEST_DELAY_SECONDS)
    return parse_rows(response.text)


def collect_sections(
    year: int,
    term: str,
    *,
    faculty: Iterable[str] = FACULTY,
    departments: Iterable[str] = DEPARTMENTS,
    session: Optional[requests.Session] = None,
) -> List[Dict[str, str]]:
    """Every undergraduate section of `departments` in the given term."""
    wanted = set(departments)
    session = session or requests.Session()
    session.headers.update(HEADERS)
    sections: Dict[tuple, Dict[str, str]] = {}

    def keep(rows: List[Dict[str, str]]) -> None:
        for row in rows:
            if row.get("DEPT_NM") in wanted and row.get("UNIV_GRSC_CLSF") == "학부":
                sections[(row.get("COURSE_NO"), row.get("DCLSS_NO"))] = row

    for name in faculty:
        keep(_search(session, year, term, staff_name=name))
    for course_name in sorted({row["SBJT_KOR_NM"] for row in sections.values() if row.get("SBJT_KOR_NM")}):
        keep(_search(session, year, term, course_name=course_name))
    return sorted(sections.values(), key=lambda r: (r.get("DEPT_NM", ""), r.get("SBJT_KOR_NM", ""), r.get("DCLSS_NO", "")))


def _credits(text: str) -> str:
    match = re.fullmatch(r"(\d+)-(\d+)-(\d+)", text or "")
    if not match:
        return text or "정보 없음"
    total, theory, practice = match.groups()
    return f"{total}학점(이론 {theory}시간, 실습 {practice}시간)"


def build_documents(sections: List[Dict[str, str]], year: int, term: str) -> List[Dict[str, object]]:
    """One crawler-document-1.0 per (department, course) with all its sections."""
    crawled_at = datetime.now(timezone.utc)
    term_label = f"{year}학년도 {TERM_LABELS[term]}"
    grouped: Dict[tuple, List[Dict[str, str]]] = defaultdict(list)
    for row in sections:
        grouped[(row.get("DEPT_NM", ""), row.get("COURSE_NO", ""), row.get("SBJT_KOR_NM", ""))].append(row)

    documents: List[Dict[str, object]] = []
    for (department, course_no, course_name), rows in sorted(grouped.items()):
        first = rows[0]
        lines = [
            f"{term_label} {department} 개설 과목: {course_name} (학수번호 {course_no})",
            f"이수구분: {first.get('SBJT_FG_NM') or '정보 없음'}",
            f"학점: {_credits(first.get('PNT_THEO_PRAC', ''))}",
            f"분반 {len(rows)}개:",
        ]
        for row in sorted(rows, key=lambda r: r.get("DCLSS_NO", "")):
            extras = [row.get("LSN_MTHD_FG") or ""]
            if row.get("ENG_YN") == "Y" and row.get("KOR_YN") != "Y":
                extras.append("영어강의")
            lines.append(
                f"- {row.get('DCLSS_NO')}분반: {row.get('STAFF_NM') or '담당교수 미정'} 교수"
                + (f" ({', '.join(e for e in extras if e)})" if any(extras) else "")
            )
        lines.append("강의계획서 원문(주차별 내용·평가 방법)은 부경대 홈페이지 > 강의계획서 조회에서 확인할 수 있습니다.")
        documents.append(apply_common_schema(
            {
                "url": SCREEN_URL,
                "title": f"{term_label} {course_name} ({department}) 개설 정보",
                "category": "강의계획서",
                "subcategory": department,
                "tags": ["강의", "개설과목", department, course_name],
                "content": "\n".join(lines),
            },
            source_dataset="pknu_syllabus",
            source_id=f"{year}-{term}-{department}-{course_no}",
            source_site=BASE_URL,
            document_type="static_page",
            content_source="irumi_course_list",
            crawled_at=crawled_at,
            metadata={
                "academic_year": year, "semester": int(term), "department": department,
                "course_no": course_no, "course_name": course_name,
                "sections": [
                    {"section": r.get("DCLSS_NO"), "professor": r.get("STAFF_NM"),
                     "method": r.get("LSN_MTHD_FG"), "credits": r.get("PNT_THEO_PRAC"),
                     "course_type": r.get("SBJT_FG_NM")}
                    for r in rows
                ],
            },
        ))
    return documents


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--term", choices=sorted(TERM_CODES), required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    sections = collect_sections(args.year, args.term)
    documents = build_documents(sections, args.year, args.term)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(documents, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"sections={len(sections)} courses={len(documents)} saved={args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

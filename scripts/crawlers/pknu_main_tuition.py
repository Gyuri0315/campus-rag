"""Read the public, condition-driven tuition tables embedded in /main/102.

The iframe's initial HTML contains empty grid placeholders. Its JavaScript
requests the four JSON lists below after a tab or search condition is chosen.
This module never treats those placeholders as tuition data.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from io import BytesIO
import re
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile

from bs4 import BeautifulSoup


IFRAME_URL = "https://irumi.pknu.ac.kr/uni/enro/uniEnroView"
API_BASE = "https://irumi.pknu.ac.kr/uni/enro/"
TUITION_NOTICE_URL = "https://icms.pknu.ac.kr/pknupasw/3153?action=view&no=9991519"
AMOUNTS_URL = "https://icms.pknu.ac.kr/boardDownload.do?no=8002137"
CLASSIFICATION_URL = "https://icms.pknu.ac.kr/boardDownload.do?no=8002140"
MAX_JSON_BYTES = 2 * 1024 * 1024
MAX_XLSX_BYTES = 2 * 1024 * 1024
XLSX_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def _response_bytes(response, limit: int) -> bytes:
    data = bytearray()
    for chunk in response.iter_content(16384):
        if len(data) + len(chunk) > limit:
            raise ValueError("response exceeds size limit")
        data.extend(chunk)
    return bytes(data)


def _get(session, url: str, referer: str, *, limit: int) -> bytes:
    with session.get(url, timeout=30, stream=True, headers={"Referer": referer}) as response:
        if response.status_code != 200:
            raise ValueError(f"HTTP_{response.status_code}: {url}")
        if response.url != url:
            raise ValueError(f"unexpected redirect: {response.url}")
        return _response_bytes(response, limit)


def _post_list(session, name: str, payload: dict) -> list[dict]:
    url = API_BASE + name
    with session.post(url, json=payload, timeout=30, stream=True,
                      headers={"Referer": IFRAME_URL, "Accept": "application/json"}) as response:
        if response.status_code != 200:
            raise ValueError(f"HTTP_{response.status_code}: {url}")
        if response.url != url or "json" not in response.headers.get("Content-Type", "").lower():
            raise ValueError(f"non-JSON or redirected response: {url}")
        import json
        body = json.loads(_response_bytes(response, MAX_JSON_BYTES))
    rows = body.get("list") if isinstance(body, dict) else None
    if not isinstance(rows, list) or not rows or not all(isinstance(row, dict) for row in rows):
        raise ValueError(f"empty or invalid data list: {url}")
    return rows


def _money(value) -> int:
    if isinstance(value, bool) or not re.fullmatch(r"\d{1,3}(?:,\d{3})*|\d+", str(value or "")):
        raise ValueError(f"invalid amount: {value!r}")
    return int(str(value).replace(",", ""))


def _xlsx_rows(payload: bytes, sheet: int) -> dict[int, dict[str, str]]:
    """Read only values from an official XLSX without adding a dependency."""
    with ZipFile(BytesIO(payload)) as workbook:
        strings_xml = ET.fromstring(workbook.read("xl/sharedStrings.xml"))
        strings = ["".join(node.text or "" for node in item.findall(".//m:t", XLSX_NS))
                   for item in strings_xml.findall("m:si", XLSX_NS)]
        xml = ET.fromstring(workbook.read(f"xl/worksheets/sheet{sheet}.xml"))
    rows = {}
    for row in xml.findall(".//m:sheetData/m:row", XLSX_NS):
        cells = {}
        for cell in row.findall("m:c", XLSX_NS):
            value = cell.find("m:v", XLSX_NS)
            if value is None or value.text is None:
                continue
            column = re.match(r"[A-Z]+", cell.attrib["r"]).group()
            cells[column] = strings[int(value.text)] if cell.get("t") == "s" else value.text
        rows[int(row.attrib["r"])] = cells
    return rows


def _official_checks(session, academic_year: int, tuition: list[dict],
                     departments: list[dict]) -> dict:
    checks = {"amounts_url": AMOUNTS_URL, "classification_url": CLASSIFICATION_URL,
              "amount_rows_checked": 0, "amount_rows_matched": 0,
              "classification_rows_checked": 0, "classification_rows_matched": 0,
              "mismatches": [], "unverified": [], "examples": []}
    if academic_year != 2026:
        checks["unverified"].append("OFFICIAL_REFERENCE_YEAR_DIFFERS")
        return checks
    try:
        amounts = _xlsx_rows(_get(session, AMOUNTS_URL, TUITION_NOTICE_URL,
                                  limit=MAX_XLSX_BYTES), 1)
        classification_bytes = _get(session, CLASSIFICATION_URL, TUITION_NOTICE_URL,
                                    limit=MAX_XLSX_BYTES)
        undergraduate = _xlsx_rows(classification_bytes, 1)
        graduate = _xlsx_rows(classification_bytes, 2)
    except (OSError, ValueError, KeyError, BadZipFile, ET.ParseError, IndexError) as exc:
        checks["unverified"].append(f"OFFICIAL_REFERENCE_UNAVAILABLE: {type(exc).__name__}: {exc}")
        return checks

    # Representative rows cover two undergraduate categories and both
    # entrance-fee cases for general graduate engineering. Cell coordinates
    # are kept so a reviewer can reproduce every comparison in the workbook.
    reference = [
        ("학부", "이학계열", "new", 8, "B", "C", "D", None),
        ("학부", "이학계열", "continuing", 8, "B", "C", "D", None),
        ("학부", "공학계열", "new", 10, "B", "C", "D", None),
        ("학부", "공학계열", "continuing", 10, "B", "C", "D", None),
        ("일반대학원", "공학계열", "new", 49, "C", "D", "E", "B"),
        ("일반대학원", "공학계열", "continuing", 58, "B", "C", "D", None),
    ]
    index = {(r["university_or_college"], r["category"], r["student_type"]): r
             for r in tuition}
    for degree, category, student_type, line, first, second, total, admission in reference:
        key = (degree, category, student_type)
        actual = index.get(key)
        if actual is None:
            checks["unverified"].append(f"REFERENCE_ROW_NOT_FOUND: {key}")
            continue
        try:
            expected = {"admission_fee_krw": _money(amounts[line][admission]) if admission else 0,
                        "tuition_krw": _money(amounts[line][first]) + _money(amounts[line][second]),
                        "total_krw": _money(amounts[line][total])}
        except (KeyError, ValueError) as exc:
            checks["unverified"].append(f"REFERENCE_CELL_UNREADABLE: {line}: {exc}")
            continue
        checks["amount_rows_checked"] += 1
        matches = all(actual[field] == value for field, value in expected.items())
        evidence = {"degree": degree, "category": category, "student_type": student_type,
                    "workbook_sheet": 1, "workbook_row": line,
                    "api_amounts": {field: actual[field] for field in expected},
                    "official_amounts": expected, "matched": matches}
        checks["examples"].append(evidence)
        if matches:
            checks["amount_rows_matched"] += 1
            actual["official_validation"] = "matched"
        else:
            actual["official_validation"] = "mismatch"
            actual["status"] = "needs_review"
            actual["warnings"].append("OFFICIAL_AMOUNT_MISMATCH")
            checks["mismatches"].append(evidence)

    # The public department search and 2026 classification workbook use
    # different labels for a major; verify the full parent+major identity.
    classification_reference = [
        ("undergraduate", "정보융합대학", "컴퓨터·인공지능공학부", "컴퓨터공학전공",
         undergraduate, 196, "L"),
        ("graduate", "대학원", "컴퓨터공학과", "컴퓨터공학과",
         graduate, 114, "K"),
    ]
    for level, college, parent, department, sheet, line, column in classification_reference:
        candidates = [r for r in departments if r["degree_level"] == level
                      and r["college"] == college and r["department_group"] == parent
                      and r["department"] == department]
        official = sheet.get(line, {})
        checks["classification_rows_checked"] += 1
        expected_name = f"{parent}({department})" if parent != department else department
        matches = (len(candidates) == 1 and official.get("A") == college
                   and official.get("B") == expected_name
                   and official.get(column) == candidates[0]["category"])
        if matches:
            checks["classification_rows_matched"] += 1
            candidates[0]["official_validation"] = "matched"
        else:
            checks["mismatches"].append({"degree_level": level, "college": college,
                                          "department": department, "workbook_row": line,
                                          "api_candidates": candidates,
                                          "official_cells": {key: official.get(key)
                                                             for key in ("A", "B", column)}})
            for candidate in candidates:
                candidate["status"] = "needs_review"
                candidate["warnings"].append("OFFICIAL_CLASSIFICATION_MISMATCH")
    return checks


def _reconcile_installments(tuition: list[dict], installments: list[dict]) -> dict:
    """Check complete payment sequences against displayed continuing tuition."""
    regular = {(row["university_or_college"], row["category_code"]): row["total_krw"]
               for row in tuition if row["student_type"] == "continuing"}
    groups: dict[tuple, list[dict]] = {}
    for row in installments:
        key = (row["university_or_college"], row["category_code"], row["grade"],
               row["installment_count"], row["domestic_foreign_type"])
        groups.setdefault(key, []).append(row)
    counts: Counter = Counter()
    unresolved = []
    for key, rows in groups.items():
        expected = regular.get(key[:2])
        sequence = {row["installment_sequence"] for row in rows}
        complete = len(rows) == key[3] and sequence == set(range(1, key[3] + 1))
        payment_sum = sum(row["total_krw"] for row in rows)
        if not complete:
            state, warning = "needs_review", "INSTALLMENT_SEQUENCE_INCOMPLETE"
        elif expected is None:
            state, warning = "needs_review", "INSTALLMENT_REGULAR_BASIS_UNVERIFIED"
        elif payment_sum != expected:
            state, warning = "needs_review", "INSTALLMENT_SUM_MISMATCH"
        else:
            state, warning = "matches_continuing_regular", None
        counts[state] += 1
        if warning:
            unresolved.append({"university_or_college": key[0], "category_code": key[1],
                               "grade": key[2], "installment_count": key[3],
                               "domestic_foreign_type": key[4], "payment_sum_krw": payment_sum,
                               "continuing_regular_total_krw": expected,
                               "warning": warning})
        for row in rows:
            row["regular_sum_check"] = state
            if warning:
                row["status"] = "needs_review"
                row["warnings"].append(warning)
    return {"group_counts": dict(counts), "unresolved_groups": unresolved,
            "note": "Matching totals are an arithmetic check, not independent verification of eligibility."}


def _verified_department_rates(tuition: list[dict], departments: list[dict]) -> list[dict]:
    """Join only departments whose 2026 category is checked in 붙임5."""
    rates = {(row["university_or_college"], row["category_code"], row["student_type"]): row
             for row in tuition if row["official_validation"] == "matched"}
    joined = []
    for department in departments:
        if department["official_validation"] != "matched":
            continue
        school = ("학부" if department["degree_level"] == "undergraduate" else
                  "일반대학원" if department["college"] == "대학원" else department["college"])
        for student_type in ("new", "continuing"):
            rate = rates.get((school, department["category_code"], student_type))
            if rate is None or rate["category"] != department["category"]:
                continue
            joined.append({**rate, "college": department["college"],
                           "department_group": department["department_group"],
                           "department": department["department"],
                           "department_code": department["department_code"],
                           "department_source_url": department["source_url"],
                           "classification_source_url": CLASSIFICATION_URL,
                           "amounts_source_url": AMOUNTS_URL,
                           "status": "verified"})
    return joined


def collect_main_102_tuition(session, page_url: str, iframe_url: str) -> dict:
    """Collect only the known public #102 iframe and its documented UI calls."""
    now = datetime.now(timezone.utc).isoformat()
    result = {"status": "needs_review", "source_url": iframe_url,
              "retrieved_at": now, "warnings": [], "tuition": [],
              "departments": [], "installments": [], "verified_department_tuition": []}
    if page_url != "https://www.pknu.ac.kr/main/102" or iframe_url != IFRAME_URL:
        result["warnings"].append("TUITION_SOURCE_UNVERIFIED")
        return result
    try:
        html = _get(session, iframe_url, page_url, limit=MAX_JSON_BYTES)
        displayed = BeautifulSoup(html, "lxml").get_text(" ", strip=True)
        term = re.search(r"<\s*(20\d{2})\s*학년도\s*([12])\s*학기\s*>", displayed)
        if not term:
            raise ValueError("academic term missing from iframe")
        year, semester = int(term[1]), int(term[2])
        result.update(academic_year=year, semester=semester, unit="KRW")

        for student_type, shyr in (("new", 0), ("continuing", 1)):
            for raw in _post_list(session, "getTuitList", {"SHYR": shyr}):
                try:
                    entrance = _money(raw["BASE_ENTR_AMT"])
                    tuition = _money(raw["BASE_LSN_AMT"])
                    total = _money(raw["BASE_TT_AMT"])
                    degree = str(raw["UNIV_GRSC_NM"]).strip()
                    category = str(raw["TUIT_PART_FG_NM"]).strip()
                    if not degree or not category:
                        raise ValueError("missing degree or category")
                except (KeyError, ValueError) as exc:
                    result["warnings"].append(f"TUITION_ROW_INVALID: {exc}")
                    continue
                warnings = [] if entrance + tuition == total else ["TUITION_TOTAL_MISMATCH"]
                result["tuition"].append({
                    "academic_year": year, "semester": semester,
                    "degree_level": "undergraduate" if degree == "학부" else "graduate",
                    "university_or_college": degree, "category": category,
                    "category_code": raw.get("TUIT_PART_FG"), "college": None,
                    "department": None,
                    "registration_type": "regular", "student_type": student_type,
                    "admission_fee_krw": entrance, "tuition_krw": tuition,
                    "total_krw": total, "unit": "KRW",
                    "source_url": API_BASE + "getTuitList", "retrieved_at": now,
                    "status": "needs_review" if warnings else "collected",
                    "warnings": warnings, "official_validation": "not_checked",
                })

        for level, division in (("undergraduate", 1), ("graduate", 2)):
            payload = {"DEPT_NM": "", "TUIT_FG": "", "UNIV_DIV": division}
            for raw in _post_list(session, "getColgSustList", payload):
                result["departments"].append({
                    "academic_year": year, "semester": semester,
                    "degree_level": level, "college": raw.get("UP_DEPT_CD"),
                    "department_group": raw.get("UP_DEPT_CD2"),
                    "department": raw.get("UP_DEPT_CD3"),
                    "department_code": raw.get("MJ_CD"),
                    "category": raw.get("TUIT_PART_FG_NM"),
                    "category_code": raw.get("TUIT_PART_FG"),
                    "source_url": API_BASE + "getColgSustList",
                    "retrieved_at": now, "status": "collected",
                    "warnings": [], "official_validation": "not_checked",
                })

        for raw in _post_list(session, "getPpaidAmtList", {"UNIV_DIV": ""}):
            try:
                row_year = int(raw["REG_YY"])
                grade = int(raw["SHYR"])
                count = int(raw["DIVID_CNT"])
                sequence = int(raw["PPAID_OSEQ"])
                tuition = _money(raw["LSN_AMT"])
                total = _money(raw["TT_AMT"])
                if not 1 <= sequence <= count or not 1 <= grade <= 10:
                    raise ValueError("invalid installment sequence or grade")
            except (KeyError, TypeError, ValueError) as exc:
                result["warnings"].append(f"INSTALLMENT_ROW_INVALID: {exc}")
                continue
            warnings = []
            if row_year != year:
                warnings.append("INSTALLMENT_YEAR_DIFFERS_FROM_HEADING")
            if tuition != total:
                warnings.append("INSTALLMENT_TOTAL_DIFFERS_FROM_TUITION")
            result["installments"].append({
                "academic_year": row_year, "semester": semester,
                "semester_source": "iframe_heading",
                "semester_code": raw.get("REG_SHTM_CD"),
                "degree_level": "undergraduate" if raw.get("UNIV_GRSC_CD_NM") == "학부" else "graduate",
                "university_or_college": raw.get("UNIV_GRSC_CD_NM"),
                "category": raw.get("TUIT_PART_FG_NM"),
                "category_code": raw.get("TUIT_PART_FG"),
                "department": None, "registration_type": "installment",
                "student_type": None, "grade": grade, "installment_count": count,
                "installment_sequence": sequence,
                "domestic_foreign_type": raw.get("TUIT_DOMFRNR_FG_NM"),
                "admission_fee_krw": None, "tuition_krw": tuition,
                "total_krw": total, "unit": "KRW",
                "source_url": API_BASE + "getPpaidAmtList", "retrieved_at": now,
                "status": "needs_review" if warnings else "collected",
                "warnings": warnings,
            })

        result["installment_reconciliation"] = _reconcile_installments(
            result["tuition"], result["installments"])
        if result["installment_reconciliation"]["unresolved_groups"]:
            result["warnings"].append("INSTALLMENT_GROUPS_NEED_REVIEW")
        result["official_checks"] = _official_checks(session, year, result["tuition"],
                                                      result["departments"])
        result["verified_department_tuition"] = _verified_department_rates(
            result["tuition"], result["departments"])
        if result["official_checks"]["mismatches"]:
            result["warnings"].append("OFFICIAL_REFERENCE_MISMATCH")
        if result["official_checks"]["unverified"]:
            result["warnings"].append("OFFICIAL_REFERENCE_UNVERIFIED")
        if result["warnings"] or any(r["status"] == "needs_review" for group in
                                     ("tuition", "departments", "installments") for r in result[group]):
            result["status"] = "needs_review"
        else:
            result["status"] = "processed"
    except Exception as exc:
        result["warnings"].append(f"TUITION_COLLECTION_FAILED: {type(exc).__name__}: {exc}")
    result["counts"] = {group: len(result[group]) for group in
                        ("tuition", "departments", "installments", "verified_department_tuition")}
    result["review_counts"] = {group: sum(row["status"] == "needs_review" for row in result[group])
                               for group in ("tuition", "departments", "installments")}
    result["warning_counts"] = dict(Counter(warning for group in ("tuition", "departments", "installments")
                                     for row in result[group] for warning in row["warnings"]))
    return result

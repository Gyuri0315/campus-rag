"""Collect the public organization widget used by /main/533."""

from __future__ import annotations

from datetime import datetime, timezone
import re

from bs4 import BeautifulSoup


PAGE_URL = "https://www.pknu.ac.kr/main/533"
WIDGET_URL = "https://www.pknu.ac.kr/org/getMngList.do"
DEPARTMENT_ID = 249
MAX_JSON_BYTES = 512 * 1024


def _text(value) -> str:
    return re.sub(r"\s+", " ", value.get_text(" ", strip=True)).strip() if value else ""


def _contact_fields(root) -> dict[str, str]:
    contacts = {}
    for group in root.select(".con05_sub_wrap > div"):
        lists = group.find_all("ul", recursive=False)
        if len(lists) != 2:
            continue
        labels = [_text(item) for item in lists[0].find_all("li", recursive=False)]
        values = [_text(item) for item in lists[1].find_all("li", recursive=False)]
        contacts.update(zip(labels, values))
    return {"location": contacts.get("위치", ""),
            "phone": contacts.get("전화번호", ""),
            "fax": contacts.get("팩스번호", "")}


def collect_main_533_org(session, page_url: str, html: str) -> dict:
    """Fetch staff rows with exactly the widget request visible in page JS."""
    now = datetime.now(timezone.utc).isoformat()
    result = {"status": "needs_review", "review_status": "pending",
              "page_id": 533, "url": page_url, "source_url": WIDGET_URL,
              "retrieved_at": now, "warnings": [], "staff": []}
    if page_url != PAGE_URL:
        result["warnings"].append("ORG_PAGE_URL_UNVERIFIED")
        return result
    soup = BeautifulSoup(html, "lxml")
    root = soup.select_one("#subCont .content_wrap")
    if root is None:
        result["warnings"].append("ORG_CONTENT_ROOT_MISSING")
        return result
    widget = root.select_one(".getOrgMngList[data-id]")
    if widget is None or widget.get("data-id") != str(DEPARTMENT_ID):
        result["warnings"].append("ORG_WIDGET_ID_UNVERIFIED")
        return result
    title = _text(root.select_one("h4.subNameH4"))
    contact = _contact_fields(root)
    if title != "앵커사업부 소개" or not all(contact.values()):
        result["warnings"].append("ORG_STATIC_CONTENT_UNVERIFIED")
    result.update(title="앵커사업부", section_title=title,
                  contact=contact, department_id=DEPARTMENT_ID)

    try:
        with session.post(WIDGET_URL, data={"dept": str(DEPARTMENT_ID)}, timeout=30,
                          stream=True, headers={"Referer": page_url,
                                                "X-Requested-With": "XMLHttpRequest",
                                                "Accept": "application/json"}) as response:
            if response.status_code != 200:
                raise ValueError(f"HTTP_{response.status_code}")
            if response.url != WIDGET_URL or "json" not in response.headers.get("Content-Type", "").lower():
                raise ValueError("unexpected organization response or redirect")
            payload = bytearray()
            for chunk in response.iter_content(8192):
                if len(payload) + len(chunk) > MAX_JSON_BYTES:
                    raise ValueError("organization response exceeds limit")
                payload.extend(chunk)
        import json
        body = json.loads(payload)
        rows = body.get("response") if isinstance(body, dict) else None
        if not isinstance(rows, list) or not rows:
            raise ValueError("organization staff list is empty or invalid")
        seen = set()
        for raw in rows:
            if (not isinstance(raw, dict) or raw.get("dept") != DEPARTMENT_ID
                    or raw.get("deptNm") != "앵커사업부"):
                raise ValueError("staff row belongs to unexpected department")
            record_id = raw.get("mngSeq")
            if not isinstance(record_id, int) or record_id in seen:
                raise ValueError("missing or duplicate staff record ID")
            seen.add(record_id)
            staff = {"source_record_id": record_id, "sort": raw.get("sort"),
                     "team": str(raw.get("belongs") or "").strip(),
                     "position": str(raw.get("position") or "").strip(),
                     "phone": str(raw.get("tel") or "").strip(),
                     "responsibilities": str(raw.get("jobDesc") or "").replace("\r\n", "\n").replace("\r", "\n").strip(),
                     "source_url": WIDGET_URL, "retrieved_at": now}
            # The public page displays these four fields. Names and e-mail
            # exist in some API records but are not displayed by this widget.
            if not staff["team"] or not staff["position"] or not staff["phone"]:
                result["warnings"].append("ORG_STAFF_FIELD_MISSING")
            result["staff"].append(staff)
        result["staff"].sort(key=lambda item: (item["sort"] if isinstance(item["sort"], int) else 10**9,
                                               item["source_record_id"]))
    except Exception as exc:
        result["warnings"].append(f"ORG_WIDGET_FAILED: {type(exc).__name__}: {exc}")
    result["staff_count"] = len(result["staff"])
    if result["staff_count"] and not result["warnings"]:
        result["status"] = "collected"
    return result

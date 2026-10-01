"""Reusable structure extraction for the main site's static CMS pages."""

from __future__ import annotations

import re
from urllib.parse import urljoin, urlsplit

from bs4 import NavigableString, Tag


FLOW_CLASSES = {"daStep": ".dasCont", "stFlw_B": ".stfCont_B",
                "stFlw": ".stfCont"}


def clean_text(tag: Tag) -> str:
    return re.sub(r"\s+", " ", tag.get_text(" ", strip=True)).strip()


def parse_table(table: Tag) -> dict:
    rows = []
    occupied: set[tuple[int, int]] = set()
    direct_rows = [tr for tr in table.select("tr") if tr.find_parent("table") is table]
    for row_index, tr in enumerate(direct_rows):
        cells = []
        column = 0
        for cell in tr.find_all(["th", "td"], recursive=False):
            while (row_index, column) in occupied:
                column += 1
            rowspan = int(cell.get("rowspan") or 1)
            colspan = int(cell.get("colspan") or 1)
            if not 1 <= rowspan <= 100 or not 1 <= colspan <= 100:
                raise ValueError("invalid table span")
            for row in range(row_index, row_index + rowspan):
                for col in range(column, column + colspan):
                    if (row, col) in occupied:
                        raise ValueError("overlapping table span")
                    occupied.add((row, col))
            cells.append({"row": row_index, "column": column,
                          "rowspan": rowspan, "colspan": colspan,
                          "header": cell.name == "th", "text": clean_text(cell)})
            column += colspan
        rows.append(cells)
    if not rows or any(not row for row in rows):
        raise ValueError("empty table row")
    width = max(col for _, col in occupied) + 1
    if any(row >= len(rows) for row, _ in occupied) or any(
        (row, col) not in occupied for row in range(len(rows)) for col in range(width)
    ):
        raise ValueError("irregular table grid")
    caption = table.find("caption", recursive=False)
    return {"type": "table", "caption": clean_text(caption) if caption else None,
            "row_count": len(rows), "column_count": width,
            "rows": rows}


def parse_flow(flow: Tag) -> dict:
    kind = next((name for name in FLOW_CLASSES if name in flow.get("class", [])), None)
    if kind is None:
        raise ValueError("unsupported process diagram")
    steps = []
    for index, card in enumerate(flow.select(FLOW_CLASSES[kind]), 1):
        heading = card.select_one("h5")
        paragraphs = [clean_text(p) for p in card.select("p")]
        if (not paragraphs or not all(paragraphs)
                or (kind != "stFlw" and (heading is None or not clean_text(heading)))):
            raise ValueError("process card changed")
        number = card.select_one("em")
        step_heading = clean_text(heading) if heading else paragraphs[0]
        description = " / ".join(paragraphs if heading else paragraphs[1:])
        steps.append({"order": index, "display_number": clean_text(number) if number else None,
                      "heading": step_heading, "description": description})
    if not steps:
        raise ValueError("empty process diagram")
    return {"type": "flow", "layout": kind, "steps": steps,
            "transitions": [{"from_order": index, "to_order": index + 1}
                            for index in range(1, len(steps))]}


def parse_organization_chart(chart: Tag) -> dict:
    """Keep the reporting hierarchy encoded by the CMS org chart headings."""
    nodes: list[dict] = []
    parents: list[tuple[int, int]] = []
    for child in chart.children:
        if not isinstance(child, Tag):
            continue
        if child.name in {"h3", "h4", "h5", "h6"}:
            level = int(child.name[1]) - 3
            while parents and parents[-1][0] >= level:
                parents.pop()
            if level and not parents:
                raise ValueError("organization chart parent missing")
            label = clean_text(child)
            if not label:
                raise ValueError("empty organization chart node")
            order = len(nodes) + 1
            nodes.append({"order": order, "level": level, "text": label,
                          "parent_order": parents[-1][1] if parents else None})
            parents.append((level, order))
        elif child.name in {"ul", "ol"}:
            if not parents:
                raise ValueError("organization chart branch has no parent")
            for item in child.find_all("li", recursive=False):
                label = clean_text(item)
                if not label:
                    raise ValueError("empty organization chart branch")
                nodes.append({"order": len(nodes) + 1, "level": parents[-1][0] + 1,
                              "text": label, "parent_order": parents[-1][1]})
    if not nodes or nodes[0]["level"] != 0:
        raise ValueError("empty organization chart")
    return {"type": "organization_chart", "layout": "orgCont", "nodes": nodes}


def table_reference_links(container: Tag, page_url: str) -> tuple[dict, ...]:
    """Record table link targets and row labels without visiting the targets."""
    links = []
    for table in container.select("table"):
        for row in table.select("tr"):
            if row.find_parent("table") is not table:
                continue
            cells = row.find_all(["th", "td"], recursive=False)
            for cell in cells:
                for anchor in cell.select("a[href]"):
                    target = urljoin(page_url, anchor["href"].strip())
                    parsed = urlsplit(target)
                    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                        continue
                    links.append({"label": clean_text(anchor), "url": target,
                                  "context": [clean_text(other) for other in cells
                                              if other is not cell and clean_text(other)]})
    return tuple(links)


def _blocks(element: Tag) -> list[dict]:
    if element.name in {"script", "style", "noscript"}:
        return []
    if element.name == "img":
        src = element.get("data-src") or element.get("src")
        return [{"type": "image", "src": src, "alt": element.get("alt") or ""}] if src else []
    if element.name == "table":
        if element.select_one("table"):
            blocks = []
            for child in element.find_all(recursive=False):
                blocks.extend(_blocks(child))
            return blocks
        if not element.select("th, td") and not clean_text(element):
            return []
        return [parse_table(element)]
    if any(name in element.get("class", []) for name in FLOW_CLASSES):
        return [parse_flow(element)]
    if "orgCont" in element.get("class", []):
        return [parse_organization_chart(element)]
    if element.name in {"ul", "ol"} and element.find("li", recursive=False):
        items = []
        for child in element.children:
            if not isinstance(child, Tag):
                continue
            if child.name == "li":
                if child.select_one("table, ul, ol, .daStep, .stFlw_B, .stFlw, img[src], img[data-src]"):
                    items.append({"blocks": _blocks(child)})
                else:
                    items.append(clean_text(child))
            elif child.name in {"ul", "ol"}:
                # This CMS sometimes places a nested list directly in a list,
                # after its parent li rather than inside it.
                nested = _blocks(child)
                if items:
                    previous = items[-1]
                    if isinstance(previous, str):
                        items[-1] = {"blocks": [{"type": "paragraph", "text": previous}, *nested]}
                    else:
                        previous["blocks"].extend(nested)
                else:
                    items.append({"blocks": nested})
        return [{"type": "list", "items": items}]
    children = list(element.children)
    if element.select_one("table, ul, ol, p, dl, .daStep, .stFlw_B, .stFlw, img[src], img[data-src]"):
        blocks = []
        for child in children:
            if isinstance(child, Tag):
                blocks.extend(_blocks(child))
            elif isinstance(child, NavigableString) and child.strip():
                blocks.append({"type": "paragraph", "text": str(child).strip()})
        return blocks
    content = clean_text(element)
    return [{"type": "paragraph", "text": content}] if content else []


def _render_blocks(blocks: list[dict]) -> list[str]:
    lines = []
    for block in blocks:
        if block["type"] == "paragraph":
            lines.append(block["text"])
        elif block["type"] == "list":
            for item in block["items"]:
                if isinstance(item, str):
                    lines.append("- " + item)
                    continue
                nested = _render_blocks(item["blocks"])
                if nested:
                    lines.append("- " + nested[0])
                    lines.extend(nested[1:])
        elif block["type"] == "table":
            if block["caption"]:
                lines.append(block["caption"])
            lines.extend(" / ".join(cell["text"] for cell in row)
                         for row in block["rows"])
        elif block["type"] == "flow":
            lines.extend(f'{step["order"]}. {step["heading"]}'
                         + (f': {step["description"]}' if step["description"] else '')
                         for step in block["steps"])
        elif block["type"] == "organization_chart":
            lines.extend("  " * node["level"] + "- " + node["text"]
                         for node in block["nodes"])
        elif block["type"] == "image":
            lines.append("[이미지]" + (f' {block["alt"]}' if block["alt"] else ""))
    return lines


def render_sections(sections: list[dict]) -> str:
    parts = []
    for section in sections:
        lines = ([section["heading"]] if section["heading"] else []) + _render_blocks(section["blocks"])
        if lines:
            parts.append("\n".join(lines))
    return "\n\n".join(parts)


def _count_blocks(blocks: list[dict], kind: str) -> int:
    count = 0
    for block in blocks:
        count += block["type"] == kind
        if block["type"] == "list":
            count += sum(_count_blocks(item["blocks"], kind) for item in block["items"]
                         if isinstance(item, dict))
    return count


def extract_structure(container: Tag) -> dict:
    root = container.select_one(".content_wrap") or container
    sections: list[dict] = []
    current = None
    for element in root.children:
        if isinstance(element, NavigableString):
            value = str(element).strip()
            if not value:
                continue
            if current is None:
                current = {"heading": "", "level": 0, "blocks": []}
                sections.append(current)
            current["blocks"].append({"type": "paragraph", "text": value})
            continue
        if not isinstance(element, Tag):
            continue
        if element.name == "h4" and any(
            name in element.get("class", []) for name in ("subName", "subNameH4")
        ):
            current = {"heading": clean_text(element),
                       "level": 1 if "subName" in element.get("class", []) else 2,
                       "blocks": []}
            sections.append(current)
            continue
        if current is None:
            current = {"heading": "", "level": 0, "blocks": []}
            sections.append(current)
        current["blocks"].extend(_blocks(element))
    if not sections:
        raise ValueError("static content has no sections")
    table_count = sum(_count_blocks(section["blocks"], "table") for section in sections)
    flow_count = sum(_count_blocks(section["blocks"], "flow") for section in sections)
    organization_chart_count = sum(_count_blocks(section["blocks"], "organization_chart")
                                   for section in sections)
    return {"sections": sections, "table_count": table_count,
            "flow_count": flow_count, "organization_chart_count": organization_chart_count,
            "content": render_sections(sections)}


def linked_file_ids(container: Tag, page_url: str, known_file_ids: set[int]) -> tuple[int, ...]:
    """Return only verified same-site file routes linked from the content tabs/body."""
    ids = []
    for anchor in container.select(".subTab a[href], .content_wrap a[href]"):
        parsed = urlsplit(urljoin(page_url, anchor["href"]))
        match = re.fullmatch(r"/main/(\d+)", parsed.path)
        if parsed.scheme != "https" or parsed.hostname != "www.pknu.ac.kr" or not match:
            continue
        page_id = int(match.group(1))
        if page_id in known_file_ids and page_id not in ids:
            ids.append(page_id)
    return tuple(ids)

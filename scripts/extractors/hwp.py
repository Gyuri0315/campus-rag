from __future__ import annotations

import logging
import os
import re
import shutil
import site
import subprocess
import sys
import sysconfig
import tempfile
import uuid
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

from scripts.extractors.common import normalize_text
from scripts.text_cleaning import clean_extracted_text

log = logging.getLogger(__name__)
HWP_EXTRACT_TIMEOUT = 60

def extract_hwpx_blocks(path: Path) -> list[dict]:
    blocks: list[dict] = []
    with zipfile.ZipFile(path) as zf:
        xml_names = [n for n in zf.namelist() if n.lower().endswith(".xml")]
        xml_names.sort()
        for name in xml_names:
            data = zf.read(name)
            try:
                root = ET.fromstring(data)
            except ET.ParseError:
                continue
            texts = []
            for elem in root.iter():
                if elem.text and normalize_text(elem.text):
                    texts.append(normalize_text(elem.text))
            if not texts:
                continue
            blocks.append(
                {
                    "type": "xml_text",
                    "style": "HWPX",
                    "section": name,
                    "text": " ".join(texts),
                }
            )
    return blocks


def is_zip_hwpx_file(path: Path) -> bool:
    try:
        head = path.read_bytes()[:4096]
    except Exception:
        return False
    lowered = head.lower()
    return head.startswith(b"PK\x03\x04") and (
        b"application/hwp+zip" in lowered or b"mimetypeapplication/hwp+zip" in lowered
    )


def is_xml_hwpml_file(path: Path) -> bool:
    try:
        head = path.read_bytes()[:4096].lstrip(b"\xef\xbb\xbf\r\n\t ")
    except Exception:
        return False
    lowered = head.lower()
    return lowered.startswith(b"<?xml") and (b"<hwpml" in lowered or b"hwpml" in lowered)


def extract_hwpml_xml_blocks(path: Path) -> list[dict]:
    raw = path.read_bytes()
    text = raw.decode("utf-8-sig", errors="replace")
    root = ET.fromstring(text)

    def tag_name(elem: ET.Element) -> str:
        return elem.tag.rsplit("}", 1)[-1].lower() if "}" in elem.tag else elem.tag.lower()

    def is_noise_payload(value: str) -> bool:
        value = value.strip()
        if not value:
            return True
        if len(value) > 4000:
            compact = re.sub(r"\s+", "", value)
            base64_chars = len(re.findall(r"[A-Za-z0-9+/=]", compact))
            if compact and base64_chars / len(compact) > 0.9:
                return True
        return False

    def collect_text(elem: ET.Element) -> str:
        parts: list[str] = []

        def walk(child: ET.Element) -> None:
            name = tag_name(child)
            if name in {"bindata", "bindatastorage", "binitem", "mappingtable", "head", "tail"}:
                if child.tail:
                    parts.append(child.tail)
                return
            if child.text:
                parts.append(child.text)
            for grandchild in list(child):
                walk(grandchild)
            if child.tail:
                parts.append(child.tail)

        walk(elem)
        return clean_extracted_text("".join(parts))

    bodies = [elem for elem in root.iter() if tag_name(elem) in {"body", "bodytext"}]
    search_roots = bodies or [root]
    blocks: list[dict] = []
    seen: set[str] = set()

    for search_root in search_roots:
        for elem in search_root.iter():
            name = tag_name(elem)
            if name not in {"p", "para", "paragraph", "row"}:
                continue
            paragraph = collect_text(elem)
            if is_noise_payload(paragraph) or paragraph in seen:
                continue
            seen.add(paragraph)
            blocks.append(
                {
                    "type": "paragraph",
                    "style": "HWPML",
                    "text": paragraph,
                }
            )

    if blocks:
        return blocks

    fallback = collect_text(root)
    if fallback and not is_noise_payload(fallback):
        return [{"type": "xml_text", "style": "HWPML", "text": fallback}]
    return []


def extract_hwp_blocks(path: Path) -> list[dict]:
    def format_hwp_runtime_error(stderr: str) -> str:
        lines = [
            line
            for line in stderr.splitlines()
            if "pkg_resources is deprecated as an API" not in line
            and "import pkg_resources" not in line
            and "setuptools.pypa.io" not in line
        ]
        err = "\n".join(lines).strip() or "unknown error"
        if "No module named 'six'" in err or 'No module named "six"' in err:
            return (
                "pyhwp runtime dependency 'six' is missing in the interpreter used by "
                "hwp5txt/hwp5html. Install it in the same environment, for example: "
                f'"{sys.executable}" -m pip install six'
            )
        return err

    if is_xml_hwpml_file(path):
        return extract_hwpml_xml_blocks(path)
    if is_zip_hwpx_file(path):
        return extract_hwpx_blocks(path)

    def resolve_hwp_command(command_name: str) -> str | None:
        resolved = shutil.which(command_name)
        if resolved:
            return resolved

        if os.name != "nt":
            return None

        exe_name = f"{command_name}.exe"
        candidate_paths = [
            Path(sys.executable).resolve().parent / "Scripts" / exe_name,
            Path(sys.executable).resolve().parent / exe_name,
            Path(sysconfig.get_path("scripts")) / exe_name,
            Path(site.getuserbase()) / "Python313" / "Scripts" / exe_name,
        ]
        for candidate in candidate_paths:
            if candidate.exists():
                return str(candidate)
        return None

    def extract_hwp_blocks_from_html(path: Path) -> list[dict]:
        hwp5html = resolve_hwp_command("hwp5html")
        if not hwp5html:
            return []

        tmp_base = Path(tempfile.gettempdir()) / "campus_rag_hwp5html"
        tmp_base.mkdir(parents=True, exist_ok=True)
        token = uuid.uuid4().hex
        copied_hwp = tmp_base / f"{token}.hwp"
        html_file = tmp_base / f"{token}.xhtml"
        try:
            shutil.copy2(path, copied_hwp)
            try:
                proc = subprocess.run(
                    [hwp5html, "--html", "--output", str(html_file), str(copied_hwp)],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="ignore",
                    check=False,
                    timeout=HWP_EXTRACT_TIMEOUT,
                )
            except subprocess.TimeoutExpired:
                log.warning("hwp5html timed out for %s; falling back to hwp5txt", path)
                return []
            if proc.returncode != 0:
                log.debug("hwp5html failed for %s: %s", path, format_hwp_runtime_error(proc.stderr))
                return []

            if not html_file.exists():
                return []

            try:
                root = ET.fromstring(html_file.read_text(encoding="utf-8", errors="ignore"))
            except ET.ParseError:
                return []

            def tag_name(elem: ET.Element) -> str:
                return elem.tag.rsplit("}", 1)[-1] if "}" in elem.tag else elem.tag

            def collect_text(elem: ET.Element) -> str:
                return normalize_text("".join(elem.itertext()))

            def table_to_text(table_elem: ET.Element) -> str:
                rows: list[str] = []
                for tr in table_elem.iter():
                    if tag_name(tr) != "tr":
                        continue
                    cells: list[str] = []
                    for child in list(tr):
                        if tag_name(child) not in {"td", "th"}:
                            continue
                        cell_text = collect_text(child)
                        if cell_text:
                            cells.append(cell_text)
                    if cells:
                        rows.append(" | ".join(cells))
                return "\n".join(rows)

            body = None
            for elem in root.iter():
                if tag_name(elem) == "body":
                    body = elem
                    break
            if body is None:
                return []

            blocks: list[dict] = []

            def collect_text_without_tables(elem: ET.Element) -> str:
                parts: list[str] = []
                if elem.text:
                    parts.append(elem.text)
                for child in list(elem):
                    if tag_name(child) != "table":
                        parts.append(collect_text_without_tables(child))
                    if child.tail:
                        parts.append(child.tail)
                return "".join(parts)

            def walk(elem: ET.Element) -> None:
                name = tag_name(elem)
                if name == "table":
                    table_text = table_to_text(elem)
                    if table_text:
                        blocks.append(
                            {
                                "type": "table",
                                "style": "HWP",
                                "text": table_text,
                            }
                        )
                    return
                if name == "p":
                    text = normalize_text(collect_text_without_tables(elem))
                    if text:
                        blocks.append(
                            {
                                "type": "paragraph",
                                "style": "HWP",
                                "text": text,
                            }
                        )
                for child in list(elem):
                    walk(child)

            walk(body)
            return blocks
        finally:
            try:
                copied_hwp.unlink(missing_ok=True)
            except Exception:
                pass
            try:
                html_file.unlink(missing_ok=True)
            except Exception:
                pass

    hwp5txt = resolve_hwp_command("hwp5txt")

    if not hwp5txt:
        raise RuntimeError(
            "hwp5txt command not found in PATH/current Python environment. "
            "Install pyhwp in the same interpreter and add its Scripts directory to PATH."
        )

    html_blocks = extract_hwp_blocks_from_html(path)
    if html_blocks:
        return html_blocks

    proc = subprocess.run(
        [hwp5txt, str(path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="ignore",
        check=False,
        timeout=HWP_EXTRACT_TIMEOUT,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"hwp5txt failed: {format_hwp_runtime_error(proc.stderr)}")

    lines = [normalize_text(line) for line in proc.stdout.splitlines()]
    lines = [line for line in lines if line]

    blocks = []
    for idx, line in enumerate(lines, start=1):
        blocks.append(
            {
                "type": "paragraph",
                "style": "HWP",
                "line": idx,
                "text": line,
            }
        )
    return blocks

__all__ = ["extract_hwp_blocks", "extract_hwpml_xml_blocks", "extract_hwpx_blocks"]

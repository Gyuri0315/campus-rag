import fitz
import json
import re
from pathlib import Path
from collections import Counter


def normalize_text(text: str) -> str:
    if not text:
        return ""

    text = text.replace("\u00a0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()


def is_likely_page_number(line: str) -> bool:
    line = line.strip()
    return bool(re.fullmatch(r"-?\s*\d+\s*-?", line))


def detect_repeated_headers_footers(page_lines_list: list[list[str]], min_repeat: int = 3) -> set[str]:
    candidates = []

    for lines in page_lines_list:
        if not lines:
            continue

        top = lines[:2]
        bottom = lines[-2:] if len(lines) >= 2 else lines

        for line in top + bottom:
            line = normalize_text(line)
            if line:
                candidates.append(line)

    counter = Counter(candidates)

    repeated = {
        line for line, count in counter.items()
        if count >= min_repeat
    }

    return repeated


def should_merge(prev_line: str, curr_line: str) -> bool:
    """
    이전 줄과 현재 줄을 하나의 문단/문장으로 이어붙일지 판단
    """
    prev_line = prev_line.strip()
    curr_line = curr_line.strip()

    if not prev_line or not curr_line:
        return False

    if prev_line.endswith((".", "!", "?", ":", ";")):
        return False

    if len(prev_line) < 20:
        return False

    if re.match(r"^[a-z0-9(\[\-]", curr_line):
        return True

    return True


def merge_lines_into_paragraphs(lines: list[str]) -> list[str]:
    paragraphs = []
    buffer = []

    for line in lines:
        line = normalize_text(line)

        if not line:
            if buffer:
                paragraphs.append(" ".join(buffer).strip())
                buffer = []
            continue

        if is_likely_page_number(line):
            continue

        if not buffer:
            buffer.append(line)
            continue

        prev_line = buffer[-1]

        if should_merge(prev_line, line):
            buffer.append(line)
        else:
            paragraphs.append(" ".join(buffer).strip())
            buffer = [line]

    if buffer:
        paragraphs.append(" ".join(buffer).strip())

    return paragraphs


def remove_consecutive_duplicate_paragraphs(paragraphs: list[str]) -> list[str]:
    cleaned = []
    prev = None

    for para in paragraphs:
        para = normalize_text(para)
        if not para:
            continue

        if para != prev:
            cleaned.append(para)

        prev = para

    return cleaned


def extract_pdf_paragraph_blocks(pdf_path: str) -> list[dict]:
    doc = fitz.open(pdf_path)

    all_page_lines = []

    for page in doc:
        text = page.get_text("text")
        lines = [normalize_text(line) for line in text.splitlines()]
        lines = [line for line in lines if line]
        all_page_lines.append(lines)

    repeated_headers_footers = detect_repeated_headers_footers(all_page_lines)

    blocks = []

    for page_num, lines in enumerate(all_page_lines, start=1):
        cleaned_lines = []

        for line in lines:
            line = normalize_text(line)

            if not line:
                continue

            if line in repeated_headers_footers:
                continue

            if is_likely_page_number(line):
                continue

            cleaned_lines.append(line)

        paragraphs = merge_lines_into_paragraphs(cleaned_lines)
        paragraphs = remove_consecutive_duplicate_paragraphs(paragraphs)

        for para in paragraphs:
            blocks.append({
                "type": "paragraph",
                "style": "Normal",
                "page": page_num,
                "text": para
            })

    return blocks


def save_preprocessed_pdf(pdf_path: str, output_path: str):
    blocks = extract_pdf_paragraph_blocks(pdf_path)

    result = {
        "source_file": Path(pdf_path).name,
        "num_blocks": len(blocks),
        "blocks": blocks
    }

    output_file = Path(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    return result


if __name__ == "__main__":
    pdf_path = r"input/pdf/test_pdf2.pdf"
    output_path = r"output/json/preprocessed_pdf2.json"

    result = save_preprocessed_pdf(pdf_path, output_path)
    print(f"Saved successfully: {output_path}")
    print(f"Preprocessed blocks: {result['num_blocks']}")
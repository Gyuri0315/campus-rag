"""Select a compact, answer-focused excerpt from a retrieved source chunk."""

from __future__ import annotations

import re


_WORD_RE = re.compile(r"[0-9A-Za-z\uac00-\ud7a3]{2,}")
_SENTENCE_RE = re.compile(r"[^.!?\n]+(?:[.!?]+|$)")
_CITATION_RE_TEMPLATE = r"\[{index}\]"


def _normalize(text: str) -> str:
    return " ".join(str(text or "").replace("\u00a0", " ").split()).strip()


def _citation_context(answer: str, source_index: int) -> str:
    marker = re.compile(_CITATION_RE_TEMPLATE.format(index=source_index))
    sentences = [
        _normalize(match.group(0))
        for match in _SENTENCE_RE.finditer(str(answer or ""))
        if marker.search(match.group(0))
    ]
    return " ".join(sentences)


def _search_terms(text: str) -> set[str]:
    terms: set[str] = set()
    for word in _WORD_RE.findall(text.lower()):
        terms.add(word)
        # Korean particles often make exact word matching brittle. Short
        # character fragments retain useful overlap without another model call.
        if len(word) >= 4:
            terms.update(word[i : i + 3] for i in range(len(word) - 2))
    return terms


def _score(text: str, terms: set[str]) -> int:
    lower = text.lower()
    return sum(len(term) ** 2 for term in terms if term in lower)


def _split_units(content: str, max_chars: int) -> list[str]:
    units: list[str] = []
    for match in _SENTENCE_RE.finditer(str(content or "")):
        sentence = _normalize(match.group(0))
        if not sentence:
            continue
        if len(sentence) <= max_chars:
            units.append(sentence)
            continue

        step = max(1, max_chars // 2)
        starts = list(range(0, len(sentence), step))
        if starts and starts[-1] + max_chars < len(sentence):
            starts.append(len(sentence) - max_chars)
        for start in starts:
            window = sentence[start : start + max_chars].strip()
            if window and (not units or window != units[-1]):
                units.append(window)
            if start + max_chars >= len(sentence):
                break
    return units


def _with_ellipsis(text: str, *, leading: bool, trailing: bool, max_chars: int) -> str:
    prefix = "… " if leading else ""
    suffix = " …" if trailing else ""
    available = max(0, max_chars - len(prefix) - len(suffix))
    body = text[:available].rstrip()
    return f"{prefix}{body}{suffix}".strip()


def extract_relevant_excerpt(
    content: str,
    *,
    question: str,
    answer: str,
    source_index: int,
    max_chars: int = 320,
) -> str:
    """Return the source passage most relevant to its cited answer sentence.

    The cited sentence (for example, the sentence containing ``[2]``) is used
    as the primary anchor. The user question and full answer provide a fallback
    when a source was retrieved but not explicitly cited.
    """

    normalized_content = _normalize(content)
    if max_chars <= 0 or not normalized_content:
        return ""
    if len(normalized_content) <= max_chars:
        return normalized_content

    cited_answer = _citation_context(answer, source_index)
    anchor = " ".join(
        part for part in (_normalize(question), cited_answer or _normalize(answer)) if part
    )
    terms = _search_terms(anchor)
    units = _split_units(content, max_chars)
    if not units:
        return _with_ellipsis(
            normalized_content,
            leading=False,
            trailing=True,
            max_chars=max_chars,
        )

    scores = [_score(unit, terms) for unit in units]
    best_index = max(range(len(units)), key=lambda index: scores[index])
    selected_start = best_index
    selected_end = best_index
    selected_length = len(units[best_index])

    while True:
        candidates: list[tuple[int, str, int]] = []
        if selected_start > 0:
            candidates.append((scores[selected_start - 1], "left", selected_start - 1))
        if selected_end + 1 < len(units):
            candidates.append((scores[selected_end + 1], "right", selected_end + 1))
        candidates.sort(reverse=True)

        expanded = False
        for _, direction, index in candidates:
            added_length = len(units[index]) + 1
            if selected_length + added_length > max_chars:
                continue
            if direction == "left":
                selected_start = index
            else:
                selected_end = index
            selected_length += added_length
            expanded = True
            break
        if not expanded:
            break

    excerpt = " ".join(units[selected_start : selected_end + 1])
    return _with_ellipsis(
        excerpt,
        leading=selected_start > 0,
        trailing=selected_end < len(units) - 1,
        max_chars=max_chars,
    )

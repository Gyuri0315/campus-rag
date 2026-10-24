"""Summarize eval_ask JSONL output for at-a-glance quality review."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


def normalize_url(url: str) -> str:
    return re.sub(r"[?#].*$", "", (url or "").strip().lower())


NOISE_PATTERNS = (
    re.compile(r"([가-힣])\1{4,}"),
    re.compile(r"[ㆍ·．\.]{5,}"),
    re.compile(r"[ㅇㅁㅋㅎㅠㅜ]{4,}"),
)


def looks_noisy(text: str) -> bool:
    if not text:
        return True
    compact = re.sub(r"\s+", "", text)
    if len(compact) < 40:
        return True
    for pattern in NOISE_PATTERNS:
        if pattern.search(text):
            return True
    return False


def summarize(path: Path) -> None:
    with path.open("r", encoding="utf-8") as f:
        rows = [json.loads(line) for line in f if line.strip()]
    print(f"# {path.name}  ({len(rows)} questions)\n")
    for index, row in enumerate(rows, start=1):
        question = row.get("question", "")
        answer = (row.get("answer") or "").strip()
        sources = row.get("sources") or []
        elapsed = row.get("elapsed_seconds")
        top_sim = row.get("top_similarity")
        urls = [normalize_url(s.get("uri") or "") for s in sources]
        uniq_urls = len({u for u in urls if u})
        dup_urls = len(urls) - uniq_urls
        noisy_count = sum(1 for s in sources if looks_noisy(s.get("content") or ""))
        no_info = "관련 정보를 찾을 수 없습니다" in answer
        preview = re.sub(r"\s+", " ", answer)[:200]
        print(f"[{index}] {question}")
        print(
            f"    src={len(sources):>2}  uniq_urls={uniq_urls:>2}  dup={dup_urls:>2}  "
            f"noisy_chunks={noisy_count:>2}  top_sim={top_sim if top_sim is None else round(top_sim, 3)}  "
            f"elapsed={elapsed}s  no_info={no_info}"
        )
        print(f"    ans: {preview}")
        print()


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    summarize(args.path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Evaluate the backend /ask API with JSONL question sets."""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen


DEFAULT_BASE_URL = "http://localhost:8000"
DEFAULT_QUESTIONS_PATH = Path("eval/questions.jsonl")
DEFAULT_OUTPUT_PATH = Path("outputs/eval_results.jsonl")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_no}: invalid JSONL: {exc}") from exc
            if not isinstance(row, dict):
                raise ValueError(f"{path}:{line_no}: each JSONL row must be an object")
            if not str(row.get("question") or "").strip():
                raise ValueError(f"{path}:{line_no}: missing question")
            rows.append(row)
    return rows


def _post_ask(base_url: str, question: str, timeout: float) -> tuple[int | None, dict[str, Any]]:
    url = urljoin(base_url.rstrip("/") + "/", "ask")
    body = json.dumps({"question": question}, ensure_ascii=False).encode("utf-8")
    request = Request(
        url,
        data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = response.read().decode("utf-8")
            return response.status, json.loads(payload)
    except HTTPError as exc:
        payload = exc.read().decode("utf-8", errors="replace")
        try:
            data: dict[str, Any] = json.loads(payload)
        except json.JSONDecodeError:
            data = {"detail": payload}
        return exc.code, {"error": data}
    except (URLError, TimeoutError, json.JSONDecodeError) as exc:
        return None, {"error": str(exc)}


def _top_similarity(sources: list[Any]) -> float | None:
    if not sources or not isinstance(sources[0], dict):
        return None
    value = sources[0].get("similarity")
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _result_row(
    *,
    question_row: dict[str, Any],
    status_code: int | None,
    response: dict[str, Any],
    elapsed_seconds: float,
) -> dict[str, Any]:
    sources = response.get("sources")
    if not isinstance(sources, list):
        sources = []
    error = response.get("error")
    return {
        "question": question_row.get("question", ""),
        "category": question_row.get("category", ""),
        "expected_behavior": question_row.get("expected_behavior", ""),
        "answer": response.get("answer", "") if not error else "",
        "sources": sources,
        "source_count": len(sources),
        "top_similarity": _top_similarity(sources),
        "status_code": status_code,
        "elapsed_seconds": round(elapsed_seconds, 3),
        "error": error,
    }


def run_eval(
    *,
    base_url: str,
    questions_path: Path,
    output_path: Path,
    timeout: float,
    limit: int | None,
    sleep_seconds: float,
) -> None:
    questions = _read_jsonl(questions_path)
    if limit is not None:
        questions = questions[:limit]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    ok_count = 0
    error_count = 0

    with output_path.open("w", encoding="utf-8", newline="\n") as out:
        for index, row in enumerate(questions, start=1):
            question = str(row["question"]).strip()
            started = time.perf_counter()
            status_code, response = _post_ask(base_url, question, timeout)
            elapsed = time.perf_counter() - started
            result = _result_row(
                question_row=row,
                status_code=status_code,
                response=response,
                elapsed_seconds=elapsed,
            )
            out.write(json.dumps(result, ensure_ascii=False) + "\n")
            out.flush()

            if result["error"]:
                error_count += 1
                status = f"ERROR {status_code or '-'}"
            else:
                ok_count += 1
                status = f"OK {status_code}"
            print(f"[{index}/{len(questions)}] {status} {question}")

            if sleep_seconds > 0 and index < len(questions):
                time.sleep(sleep_seconds)

    print(
        f"saved={output_path} total={len(questions)} ok={ok_count} errors={error_count}"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url",
        default=os.getenv("ASK_API_BASE_URL")
        or os.getenv("API_BASE_URL")
        or DEFAULT_BASE_URL,
        help=f"Backend API base URL. Defaults to {DEFAULT_BASE_URL}.",
    )
    parser.add_argument(
        "--questions",
        type=Path,
        default=DEFAULT_QUESTIONS_PATH,
        help=f"Input JSONL question file. Defaults to {DEFAULT_QUESTIONS_PATH}.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_PATH,
        help=f"Output JSONL result file. Defaults to {DEFAULT_OUTPUT_PATH}.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=60.0,
        help="Per-request timeout in seconds.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Evaluate only the first N questions.",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=0.0,
        help="Sleep seconds between requests.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    run_eval(
        base_url=args.base_url,
        questions_path=args.questions,
        output_path=args.output,
        timeout=args.timeout,
        limit=args.limit,
        sleep_seconds=args.sleep,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Prepare disjoint crawl queues and run one queue in each terminal/process."""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import ExitStack, contextmanager
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from urllib.parse import urlsplit

from scripts.crawlers.common.schema import now_kst
from scripts.crawlers.common.storage import get_dataset_paths
from scripts.crawlers.departments.config import load_registry
from scripts.crawlers.departments.registry_ops import save_json_atomic

ROOT = Path(__file__).resolve().parents[3]


def build_plan(source: dict, registry: dict, workers: int, evidence: dict | None = None) -> dict:
    if source.get("status") == "running":
        raise ValueError("Stop the existing --all crawl gracefully before preparing parallel queues")
    if not 1 <= workers <= 4:
        raise ValueError("workers must be between 1 and 4")
    expected = list(dict.fromkeys(source["expected_datasets"]))
    expected += [name for name, config in registry.items() if config.crawl_ready and name not in expected]
    if any(name not in registry or not registry[name].crawl_ready for name in expected):
        raise ValueError("A source target is missing or disabled in the current registry")
    previous = [row for row in source.get("results", []) if row["status"] != "cancelled"]
    completed = {row["dataset"] for row in previous}
    pending = [name for name in expected if name not in completed]
    hosts = {}
    verified = {row["dataset"]: row for row in (evidence or {}).get("results", [])}
    for name in pending:
        config = registry[name]
        hosts[name] = {urlsplit(config.base_url).hostname, *config.tls_legacy_server_hosts}
        hosts[name].update(hop.get("host") for hop in verified.get(name, {}).get("hops", []))
        hosts[name].discard(None)

    # Merge connected host sets so redirects/shared CMS stay on the same worker.
    components = []
    for name in pending:
        component = {name}
        host_set = set(hosts[name])
        overlap = [item for item in components if item[1] & host_set]
        while overlap:
            for item in overlap:
                component.update(item[0])
                host_set.update(item[1])
                components.remove(item)
            overlap = [item for item in components if item[1] & host_set]
        components.append((component, host_set))

    weights = {name: max(1, sum(1 if section.kind == "board" else 0.1
                              for section in registry[name].active_sections)) for name in pending}
    groups = [{"id": index + 1, "datasets": [], "hosts": [], "weight": 0.0} for index in range(workers)]
    for names, host_set in sorted(components, key=lambda item: -sum(weights[n] for n in item[0])):
        group = min(groups, key=lambda item: (item["weight"], item["id"]))
        group["datasets"].extend(names)
        group["hosts"].extend(host_set)
        group["weight"] += sum(weights[name] for name in names)
    for group in groups:
        group["datasets"].sort(key=expected.index)
        group["hosts"] = sorted(set(group["hosts"]))
        group["weight"] = round(group["weight"], 1)
    assigned = [name for group in groups for name in group["datasets"]]
    if len(assigned) != len(set(assigned)) or set(assigned) != set(pending):
        raise ValueError("Parallel assignment would omit or duplicate a target")
    return {"created_at": now_kst(), "expected_datasets": expected,
            "previous_results": previous, "pending_datasets": pending,
            "coverage": source.get("coverage", {}), "groups": groups}


@contextmanager
def exclusive_lock(directory: Path, key: str):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (hashlib.sha256(key.encode()).hexdigest() + ".lock")
    with path.open("a+b") as handle:
        if path.stat().st_size == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def run_dataset(name: str, report: dict, report_path: Path) -> dict:
    paths = get_dataset_paths(ROOT, name, group="department")
    before = set(paths.runs.glob("*.json"))
    command = [sys.executable, "-u", "-X", "utf8", "-m",
               "scripts.crawlers.departments.engine", "--dataset", name, "--once"]
    env = {**os.environ, "CRAWLER_FILE_LOGGING": "1", "PYTHONIOENCODING": "utf-8"}
    process = subprocess.Popen(command, cwd=ROOT, env=env)
    report["child_pid"] = process.pid
    save_json_atomic(report_path, report)
    interrupted = False
    try:
        code = process.wait()
    except KeyboardInterrupt:
        # The terminal also delivers Ctrl+C to the child. Let it flush its result.
        interrupted = True
        code = process.wait()
    finally:
        report.pop("child_pid", None)
    new_results = set(paths.runs.glob("*.json")) - before
    if new_results:
        path = max(new_results, key=lambda item: item.stat().st_mtime_ns)
        result = json.loads(path.read_text(encoding="utf-8"))
        result.update(exit_code=code, result_path=path.relative_to(ROOT).as_posix())
        if interrupted:
            result["status"] = "cancelled"
        return result
    return {"dataset": name, "exit_code": code, "status": "cancelled" if interrupted else "failed",
            "error": "No run-result JSON was saved by the child process"}


def run_worker(manifest_path: Path, worker_id: int) -> int:
    plan = json.loads(manifest_path.read_text(encoding="utf-8"))
    group = next(item for item in plan["groups"] if item["id"] == worker_id)
    report_path = manifest_path.parent / f"worker_{worker_id}.json"
    old = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else {}
    finished = [row for row in old.get("results", []) if row["status"] != "cancelled"]
    done = {row["dataset"] for row in finished}
    report = {"worker": worker_id, "pid": os.getpid(), "started_at": now_kst(), "status": "running",
              "expected_datasets": group["datasets"], "results": finished, "active_dataset": None}
    lock_root = ROOT / "files/department/_runs/parallel_locks"
    with ExitStack() as stack:
        # Claims last for the queue, so duplicate terminals fail before touching data.
        for key in sorted([f"dataset:{name}" for name in group["datasets"]]
                          + [f"host:{host}" for host in group["hosts"]]):
            stack.enter_context(exclusive_lock(lock_root, key))
        save_json_atomic(report_path, report)
        try:
            for index, name in enumerate(group["datasets"], 1):
                if name in done:
                    continue
                report["active_dataset"] = name
                save_json_atomic(report_path, report)
                print(f"[Worker {worker_id}] {index}/{len(group['datasets'])}: {name}", flush=True)
                try:
                    result = run_dataset(name, report, report_path)
                except KeyboardInterrupt:
                    raise
                except Exception as exc:
                    result = {"dataset": name, "status": "failed", "error": str(exc), "exit_code": 1}
                report["results"].append(result)
                report["active_dataset"] = None
                save_json_atomic(report_path, report)
                if result["status"] == "cancelled":
                    raise KeyboardInterrupt
        except KeyboardInterrupt:
            report.update(status="cancelled", finished_at=now_kst())
            save_json_atomic(report_path, report)
            return 130
        report.update(status="needs_attention" if any(row["status"] != "success" for row in report["results"])
                      else "completed", finished_at=now_kst())
        save_json_atomic(report_path, report)
    print(f"Worker {worker_id}: {report['status']}. Report: {report_path}", flush=True)
    return 1 if any(row["status"] == "failed" for row in report["results"]) else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare", action="store_true")
    mode.add_argument("--worker", type=int)
    mode.add_argument("--status", action="store_true")
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--source-report", type=Path,
                        default=ROOT / "files/department/_runs/department_batch_report.json")
    parser.add_argument("--host-evidence", type=Path)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--output-root", type=Path)
    args = parser.parse_args()
    if args.prepare:
        if not args.output_root:
            parser.error("--prepare requires --output-root")
        source = json.loads(args.source_report.read_text(encoding="utf-8"))
        evidence = json.loads(args.host_evidence.read_text(encoding="utf-8")) if args.host_evidence else None
        plan = build_plan(source, load_registry(), args.workers, evidence)
        plan["source_report"] = str(args.source_report.resolve())
        path = args.output_root / "manifest.json"
        if path.exists():
            parser.error("output manifest already exists; use a new --output-root")
        save_json_atomic(path, plan)
        print(json.dumps({"manifest": str(path.resolve()), "pending": len(plan["pending_datasets"]),
                          "groups": plan["groups"]}, ensure_ascii=False, indent=2))
        return 0
    if not args.manifest:
        parser.error("--worker and --status require --manifest")
    if args.worker:
        return run_worker(args.manifest.resolve(), args.worker)
    plan = json.loads(args.manifest.read_text(encoding="utf-8"))
    workers = []
    for group in plan["groups"]:
        path = args.manifest.parent / f"worker_{group['id']}.json"
        r = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        workers.append({"worker": group["id"], "status": r.get("status", "not_started"),
                        "active_dataset": r.get("active_dataset"), "pid": r.get("pid"),
                        "child_pid": r.get("child_pid"), "finished": len(r.get("results", [])),
                        "total": len(group["datasets"])})
    print(json.dumps({"previously_finished": len(plan["previous_results"]),
                      "parallel_targets": len(plan["pending_datasets"]), "workers": workers},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

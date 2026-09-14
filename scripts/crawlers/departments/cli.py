"""CLI for department address validation, CMS probing, and section discovery."""

from __future__ import annotations

import argparse
import json
import logging
import re
import shutil
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.crawlers.departments.config import (  # noqa: E402
    DEFAULT_REGISTRY_PATH, DEFAULT_SITE_CATALOG_PATH, audit_registry, find_sites,
    load_registry, load_site_catalog, validate_catalog,
)
from scripts.crawlers.departments.discovery import discover_site  # noqa: E402
from scripts.crawlers.departments.probe import probe_site  # noqa: E402
from scripts.crawlers.common.logging import (  # noqa: E402
    configure_crawler_logging, log_event, set_run_id,
)
from scripts.crawlers.common.schema import RunResult  # noqa: E402
from scripts.crawlers.departments.registry_ops import (  # noqa: E402
    plan_discovery_updates, save_json_atomic,
)
from scripts.crawlers.departments.preflight import (  # noqa: E402
    build_preflight, render_preflight_table,
)
from scripts.crawlers.departments.freshness import add_result_provenance  # noqa: E402


def safe_site_key(value: str) -> str:
    return re.sub(r"[^a-z0-9._-]+", "_", value.lower()).strip("._") or "site"


def resolve_target(args: argparse.Namespace) -> tuple[str, str]:
    sites = load_site_catalog(args.sites)
    config = None
    if args.dataset:
        config = load_registry(args.registry).get(args.dataset)
        if config is None:
            raise KeyError(f"unknown department dataset: {args.dataset}")
        site_key = config.source_catalog_key
        if not site_key:
            raise ValueError(f"dataset has no source_catalog_key: {args.dataset}")
    else:
        site_key = args.site_key
    matches = find_sites(site_key, sites)
    homepage = next((site.homepage for site in matches if site.homepage), None)
    if not homepage:
        raise ValueError(f"site has no homepage: {site_key}")
    # TLS fallback datasets start directly on their configured HTTPS endpoint;
    # do not send even an initial crawler request over clear-text HTTP.
    if config and config.tls_system_trust_fallback:
        homepage = config.base_url
    return site_key, homepage


def target_config(args: argparse.Namespace):
    registry = load_registry(args.registry)
    if args.dataset:
        return registry.get(args.dataset)
    return next(
        (config for config in registry.values() if config.source_catalog_key == args.site_key),
        None,
    )


def result_path(site_key: str, filename: str, output_root: Path | None) -> Path:
    root = output_root or PROJECT_ROOT / "files" / "_discovery"
    return root / safe_site_key(site_key) / filename


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate and discover PKNU department CMS sites")
    parser.add_argument("--sites", type=Path, default=DEFAULT_SITE_CATALOG_PATH)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY_PATH)
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("validate", help="validate address catalog and crawl registry")
    list_ready = subparsers.add_parser(
        "list-ready", help="show read-only --all eligibility without network requests",
    )
    list_ready.add_argument("--json", action="store_true", dest="json_output")
    list_ready.add_argument("--output-root", type=Path, default=None,
                            help="read probe/discovery results from this directory")
    for command in ("probe", "discover"):
        child = subparsers.add_parser(command)
        target = child.add_mutually_exclusive_group(required=True)
        target.add_argument("--dataset")
        target.add_argument("--site-key")
        child.add_argument("--timeout", type=int, default=20)
        child.add_argument("--output-root", type=Path, default=None)
    discover = subparsers.choices["discover"]
    discover.add_argument("--max-sections", type=int, default=50)
    discover.add_argument("--request-delay", type=float, default=0.2)
    discover.add_argument("--force", action="store_true", help="discover even when probe is not compatible")
    for command in ("probe-all", "discover-all"):
        child = subparsers.add_parser(command, help=f"run {command.removesuffix('-all')} for every inactive dataset")
        child.add_argument("--timeout", type=int, default=20)
        child.add_argument("--output-root", type=Path, default=None)
    discover_all = subparsers.choices["discover-all"]
    discover_all.add_argument("--max-sections", type=int, default=50)
    discover_all.add_argument("--request-delay", type=float, default=0.2)
    discover_all.add_argument("--force", action="store_true")
    apply_discovery = subparsers.add_parser(
        "apply-discovery", help="plan or apply reviewed discovery files to the registry",
    )
    apply_discovery.add_argument("--dataset", action="append", dest="datasets")
    apply_discovery.add_argument("--output-root", type=Path, default=None)
    apply_discovery.add_argument("--apply", action="store_true")
    apply_discovery.add_argument("--confirm-reviewed", action="store_true")
    return parser


def batch_targets(registry, *, incomplete_only: bool = True):
    return [
        config for config in registry.values()
        if not incomplete_only or not config.crawl_ready
    ]


def run_batch(args: argparse.Namespace) -> int:
    registry = load_registry(args.registry)
    sites = load_site_catalog(args.sites)
    site_by_dataset = {site.dataset: site for site in sites if site.dataset and site.homepage}
    targets = batch_targets(registry)
    total = len(targets)
    run = RunResult(dataset="department_discovery", mode="full")
    logger, context = configure_crawler_logging("department_discovery", PROJECT_ROOT)
    set_run_id(context, run.run_id)
    started = time.monotonic()
    results = []
    log_event(
        logger, logging.INFO, "batch_started", command=args.command,
        total=total, force=bool(getattr(args, "force", False)),
    )
    for index, config in enumerate(targets, start=1):
        site = site_by_dataset.get(config.dataset)
        if not site or not site.site_key or not site.homepage:
            item = {"dataset": config.dataset, "status": "skipped", "reason": "homepage_not_found"}
            results.append(item)
            log_event(
                logger, logging.WARNING, "batch_item_skipped", dataset_name=config.dataset,
                index=index, total=total, reason=item["reason"],
            )
        else:
            item_started = time.monotonic()
            try:
                target_url = config.base_url if config.tls_system_trust_fallback else site.homepage
                log_event(
                    logger, logging.INFO, "probe_started", dataset_name=config.dataset,
                    index=index, total=total, url=target_url,
                )
                probe = probe_site(site_key=site.site_key, url=target_url, timeout=args.timeout,
                                   system_trust_fallback=config.tls_system_trust_fallback,
                                   adapter_name=config.adapter)
                probe_path = save_json_atomic(
                    result_path(site.site_key, "probe.json", args.output_root),
                    add_result_provenance({"dataset": config.dataset, **probe.to_dict()}, config),
                )
                item = {
                    "dataset": config.dataset, "probe_status": probe.status,
                    "probe_path": probe_path.as_posix(),
                }
                log_event(
                    logger, logging.WARNING if probe.status == "unreachable" else logging.INFO,
                    "probe_finished", dataset_name=config.dataset, index=index, total=total,
                    status=probe.status, confidence=probe.confidence,
                    elapsed_seconds=round(time.monotonic() - item_started, 2),
                )
                if args.command == "discover-all":
                    if not probe.compatible and not args.force:
                        item.update({"status": "skipped", "reason": "probe_not_compatible"})
                        log_event(
                            logger, logging.WARNING, "batch_item_skipped",
                            dataset_name=config.dataset, index=index, total=total,
                            reason=item["reason"], probe_status=probe.status,
                        )
                    else:
                        log_event(
                            logger, logging.INFO, "discovery_started",
                            dataset_name=config.dataset, index=index, total=total,
                            max_sections=args.max_sections,
                        )
                        discovery = discover_site(
                            site_key=site.site_key, base_url=probe.final_url or site.homepage,
                            timeout=args.timeout, max_sections=args.max_sections,
                            request_delay=args.request_delay,
                            system_trust_fallback=config.tls_system_trust_fallback,
                            adapter_name=config.adapter,
                        )
                        discovery_path = save_json_atomic(
                            result_path(site.site_key, "discovery.json", args.output_root),
                            add_result_provenance(
                                {"dataset": config.dataset, **discovery.to_dict()}, config,
                            ),
                        )
                        item.update({"status": discovery.status, "discovery_path": discovery_path.as_posix()})
                        log_event(
                            logger, logging.ERROR if discovery.status == "failed" else logging.INFO,
                            "discovery_finished", dataset_name=config.dataset,
                            index=index, total=total, status=discovery.status,
                            sections=len(discovery.sections), errors=len(discovery.errors),
                            elapsed_seconds=round(time.monotonic() - item_started, 2),
                        )
                else:
                    item["status"] = probe.status
                results.append(item)
            except Exception as exc:
                item = {"dataset": config.dataset, "status": "failed", "reason": "unexpected_error", "message": str(exc)}
                results.append(item)
                log_event(
                    logger, logging.ERROR, "batch_item_failed", dataset_name=config.dataset,
                    index=index, total=total, elapsed_seconds=round(time.monotonic() - item_started, 2),
                    exc_info=True,
                )

        successful = sum(
            result["status"] in {
                "compatible", "partial", "unsupported", "success",
                "partial_success", "no_candidates",
            }
            for result in results
        )
        failed = sum(result["status"] in {"failed", "unreachable"} for result in results)
        skipped = sum(result["status"] == "skipped" for result in results)
        log_event(
            logger, logging.INFO, "batch_progress", completed=index, total=total,
            percent=round(index / total * 100, 1) if total else 100.0,
            dataset_name=config.dataset, status=item["status"], successful=successful,
            failed=failed, skipped=skipped,
            elapsed_seconds=round(time.monotonic() - started, 2),
        )
    summary = {
        "run_id": run.run_id, "command": args.command, "total": len(results),
        "successful": sum(item["status"] in {"compatible", "partial", "unsupported", "success", "partial_success", "no_candidates"} for item in results),
        "failed": sum(item["status"] in {"failed", "unreachable"} for item in results),
        "skipped": sum(item["status"] == "skipped" for item in results),
        "elapsed_seconds": round(time.monotonic() - started, 2),
        "results": results,
    }
    output_root = args.output_root or PROJECT_ROOT / "files" / "_discovery"
    summary_path = save_json_atomic(output_root / f"{args.command}.json", summary)
    log_event(
        logger, logging.ERROR if summary["failed"] else logging.INFO, "batch_finished",
        command=args.command, total=summary["total"], successful=summary["successful"],
        failed=summary["failed"], skipped=summary["skipped"],
        elapsed_seconds=summary["elapsed_seconds"], summary_path=summary_path,
    )
    return 1 if summary["failed"] else 0


def apply_discovery(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    if args.apply and not args.confirm_reviewed:
        parser.error("--apply requires --confirm-reviewed")
    registry_payload = json.loads(args.registry.read_text(encoding="utf-8"))
    registry = load_registry(args.registry)
    datasets = set(args.datasets or [])
    unknown = datasets.difference(registry)
    if unknown:
        parser.error(f"unknown dataset(s): {', '.join(sorted(unknown))}")
    configs = [
        config for config in registry.values()
        if (not datasets or config.dataset in datasets) and not config.crawl_ready
    ]
    discovery_root = args.output_root or PROJECT_ROOT / "files" / "_discovery"
    updated, report = plan_discovery_updates(registry_payload, configs, discovery_root)
    if args.apply and report["changes"]:
        backup_path = args.registry.with_name(args.registry.name + ".bak")
        if backup_path.exists():
            parser.error(f"registry backup already exists: {backup_path}")
        shutil.copy2(args.registry, backup_path)
        save_json_atomic(args.registry, updated)
        report["dry_run"] = False
        report["registry_path"] = args.registry.as_posix()
        report["backup_path"] = backup_path.as_posix()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        registry_audit = audit_registry(args.registry)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"invalid registry: {exc}", file=sys.stderr)
        return 1
    if args.command == "list-ready":
        report = build_preflight(
            registry_audit.configs,
            discovery_root=args.output_root or PROJECT_ROOT / "files" / "_discovery",
            registry_audit=registry_audit,
        )
        if args.json_output:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            print(render_preflight_table(report))
        return 1 if registry_audit.errors else 0
    if args.command == "validate":
        try:
            report = validate_catalog(load_site_catalog(args.sites), registry_audit.configs)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"invalid registry or site catalog: {exc}", file=sys.stderr)
            return 1
        if registry_audit.errors:
            report["errors"] = [*report.get("errors", []), *registry_audit.errors]
            report["status"] = "invalid"
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["status"] == "valid" else 1
    if registry_audit.errors:
        print(json.dumps({
            "status": "invalid", "errors": registry_audit.errors,
        }, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1
    if args.command in {"probe-all", "discover-all"}:
        return run_batch(args)
    if args.command == "apply-discovery":
        return apply_discovery(args, parser)
    try:
        site_key, homepage = resolve_target(args)
    except (KeyError, ValueError) as exc:
        parser.error(str(exc))

    config = target_config(args)
    tls_fallback = bool(config and config.tls_system_trust_fallback)
    if tls_fallback:
        homepage = config.base_url
    probe = probe_site(site_key=site_key, url=homepage, timeout=args.timeout,
                       system_trust_fallback=tls_fallback,
                       adapter_name=config.adapter if config else "numeric_cms")
    probe_payload = probe.to_dict()
    if config:
        probe_payload = add_result_provenance(probe_payload, config)
    probe_path = save_json_atomic(
        result_path(site_key, "probe.json", args.output_root), probe_payload,
    )
    if args.command == "probe":
        print(json.dumps({**probe.to_dict(), "result_path": probe_path.as_posix()}, ensure_ascii=False, indent=2))
        return 0 if probe.status in {"compatible", "partial", "unsupported"} else 1
    if not probe.compatible and not args.force:
        print(json.dumps({
            "status": "skipped", "reason": "probe_not_compatible",
            "probe_status": probe.status, "probe_path": probe_path.as_posix(),
        }, ensure_ascii=False, indent=2))
        return 1
    discovery = discover_site(
        site_key=site_key, base_url=probe.final_url or homepage,
        timeout=args.timeout, max_sections=args.max_sections,
        request_delay=args.request_delay,
        system_trust_fallback=tls_fallback,
        adapter_name=config.adapter if config else "numeric_cms",
    )
    discovery_payload = discovery.to_dict()
    if config:
        discovery_payload = add_result_provenance(discovery_payload, config)
    discovery_path = save_json_atomic(
        result_path(site_key, "discovery.json", args.output_root), discovery_payload,
    )
    print(json.dumps({**discovery.to_dict(), "result_path": discovery_path.as_posix()}, ensure_ascii=False, indent=2))
    return 0 if discovery.status in {"success", "partial_success", "no_candidates"} else 1


if __name__ == "__main__":
    raise SystemExit(main())

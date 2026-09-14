"""Read-only classification and rendering for department batch preflight."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from scripts.crawlers.departments.config import DepartmentConfig
from scripts.crawlers.departments.registry_ops import safe_site_key
from scripts.crawlers.departments.freshness import (
    assess_result_freshness, probe_discovery_warnings,
)


PREFLIGHT_STATUSES = (
    "ready", "disabled", "pending_review", "blocked", "requires_adapter", "hub_only",
)


def _load_result(root: Path, config: DepartmentConfig, name: str) -> dict[str, Any]:
    if not config.source_catalog_key:
        return {}
    path = root / safe_site_key(config.source_catalog_key) / name
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def classify_preflight(
    config: DepartmentConfig, *, discovery_root: Path,
) -> dict[str, Any]:
    active_sections = len(config.active_sections)
    probe = _load_result(discovery_root, config, "probe.json")
    discovery = _load_result(discovery_root, config, "discovery.json")
    probe_freshness = assess_result_freshness(probe, config)["status"] if probe else "missing"
    discovery_freshness = assess_result_freshness(discovery, config)["status"] if discovery else "missing"
    status_warnings = probe_discovery_warnings(probe, discovery)
    if config.crawl_ready:
        status, reason = "ready", "enabled_with_active_sections"
    else:
        sections = discovery.get("sections") if isinstance(discovery.get("sections"), list) else []
        probe_error = probe.get("error") if isinstance(probe.get("error"), dict) else {}
        blocked = probe.get("status") == "blocked" or probe_error.get("code") == "ACCESS_BLOCKED"
        external = [
            section for section in sections
            if any("external redirect:" in str(warning) for warning in section.get("warnings") or [])
        ]
        hub_only = bool(external) and all(section.get("status") == "unsupported" for section in sections)
        requires_adapter = (
            discovery.get("status") == "requires_adapter"
            or any(section.get("status") == "requires_adapter" for section in sections)
            or any(
                "adapter required" in str(warning).lower()
                for section in sections for warning in section.get("warnings") or []
            )
        )
        candidates = sum(section.get("status") == "candidate" for section in sections)
        if blocked:
            status, reason = "blocked", "access_blocked"
        elif hub_only:
            status, reason = "hub_only", "only_navigation_or_external_sections"
        elif requires_adapter:
            status, reason = "requires_adapter", "discovery_requires_adapter"
        elif config.sections and not config.enabled:
            status, reason = "disabled", "configured_but_disabled"
        elif candidates:
            status, reason = "pending_review", f"{candidates}_discovery_candidates"
        elif discovery.get("status") in {"success", "partial_success"}:
            status, reason = "pending_review", "discovery_needs_review"
        else:
            status, reason = "disabled", "not_configured"
    return {
        "dataset": config.dataset,
        "status": status,
        "adapter": config.adapter,
        "active_sections": active_sections,
        "reason": reason,
        "probe_freshness": probe_freshness,
        "discovery_freshness": discovery_freshness,
        "warnings": status_warnings,
    }


def build_preflight(
    registry: dict[str, DepartmentConfig], *, discovery_root: Path,
) -> dict[str, Any]:
    items = [classify_preflight(config, discovery_root=discovery_root) for config in registry.values()]
    counts = Counter(item["status"] for item in items)
    summary = {status: counts.get(status, 0) for status in PREFLIGHT_STATUSES}
    summary.update({
        "registered": len(items),
        "operational": counts.get("ready", 0),
        "excluded": len(items) - counts.get("ready", 0),
    })
    return {
        "schema_version": "1.0",
        "items": items,
        "summary": summary,
    }


def render_preflight_table(report: dict[str, Any]) -> str:
    headers = ("STATUS", "DATASET", "ADAPTER", "SECTIONS", "RESULTS", "REASON")
    rows = [
        (
            item["status"], item["dataset"], item["adapter"], str(item["active_sections"]),
            f"P:{item['probe_freshness']}/D:{item['discovery_freshness']}", item["reason"],
        )
        for item in report["items"]
    ]
    widths = [max(len(headers[i]), *(len(row[i]) for row in rows)) for i in range(len(headers))]
    line = "  ".join(headers[i].ljust(widths[i]) for i in range(len(headers)))
    divider = "  ".join("-" * width for width in widths)
    body = ["  ".join(row[i].ljust(widths[i]) for i in range(len(headers))) for row in rows]
    summary = " ".join(f"{key}={value}" for key, value in report["summary"].items())
    return "\n".join([line, divider, *body, "", summary])

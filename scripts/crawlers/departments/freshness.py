"""Provenance and freshness checks for saved department probe/discovery results."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from scripts.crawlers.common.schema import now_kst
from scripts.crawlers.departments.adapters import get_adapter
from scripts.crawlers.departments.config import DepartmentConfig


def registry_config_hash(config: DepartmentConfig) -> str:
    adapter = get_adapter(config.adapter)
    inputs = {
        "dataset": config.dataset,
        "base_url": config.base_url,
        "source_catalog_key": config.source_catalog_key,
        "site_prefix": config.site_prefix,
        "adapter": {"name": adapter.name, "version": adapter.version},
        "tls_system_trust_fallback": config.tls_system_trust_fallback,
    }
    encoded = json.dumps(inputs, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def add_result_provenance(payload: dict[str, Any], config: DepartmentConfig) -> dict[str, Any]:
    adapter = get_adapter(config.adapter)
    result = dict(payload)
    result["provenance"] = {
        "adapter": {"name": adapter.name, "version": adapter.version},
        "registry_config_hash": registry_config_hash(config),
        "registry_url": config.base_url,
        "executed_at": now_kst(),
    }
    return result


def assess_result_freshness(payload: dict[str, Any], config: DepartmentConfig) -> dict[str, Any]:
    provenance = payload.get("provenance")
    if not isinstance(provenance, dict):
        return {"status": "legacy", "fresh": False, "reasons": ["missing_provenance"]}
    reasons: list[str] = []
    adapter = provenance.get("adapter") if isinstance(provenance.get("adapter"), dict) else {}
    current_adapter = get_adapter(config.adapter)
    if adapter.get("name") != current_adapter.name:
        reasons.append("adapter_changed")
    if adapter.get("version") != current_adapter.version:
        reasons.append("adapter_version_changed")
    if provenance.get("registry_url") != config.base_url:
        reasons.append("url_changed")
    if provenance.get("registry_config_hash") != registry_config_hash(config):
        reasons.append("registry_config_changed")
    if not provenance.get("executed_at"):
        reasons.append("missing_execution_time")
    return {"status": "stale" if reasons else "fresh", "fresh": not reasons, "reasons": reasons}


def probe_discovery_warnings(
    probe: dict[str, Any] | None, discovery: dict[str, Any] | None,
) -> list[dict[str, str]]:
    if not probe or not discovery:
        return []
    probe_status, discovery_status = probe.get("status"), discovery.get("status")
    warnings: list[dict[str, str]] = []
    if probe_status == "blocked" and discovery_status != "blocked":
        warnings.append({
            "code": "BLOCKED_PROBE_DISCOVERY_CONFLICT",
            "message": f"current probe is blocked; saved discovery status is {discovery_status!r}",
        })
    elif probe_status in {"compatible", "partial"} and discovery_status == "blocked":
        warnings.append({
            "code": "PROBE_DISCOVERY_STATUS_CONFLICT",
            "message": "probe is compatible but discovery is blocked",
        })
    return warnings

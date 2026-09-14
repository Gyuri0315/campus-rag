"""Typed configuration and registry loading for department crawlers."""

from __future__ import annotations

import json
import csv
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit


REGISTRY_SCHEMA_VERSION = "1.0"
DEFAULT_REGISTRY_PATH = Path(__file__).with_name("registry.json")
DEFAULT_SITE_CATALOG_PATH = Path(__file__).with_name("sites.csv")
SECTION_KINDS = {"board", "static_page"}
SECTION_DOCUMENT_TYPES = {
    "notice", "static_page", "guide", "law", "regulation", "bylaw", "guideline",
}
CMS_ADAPTERS = {"numeric_cms", "query_view_do", "query_view_legacy", "query_mcode", "legacy_php", "html_php"}


@dataclass(frozen=True)
class DepartmentSite:
    college: str | None
    department: str | None
    major: str | None
    homepage: str | None
    compatibility: str | None
    notes: str | None
    dataset: str | None = None

    @classmethod
    def from_row(cls, row: dict[str, str]) -> "DepartmentSite":
        def value(name: str) -> str | None:
            text = str(row.get(name) or "").strip()
            return None if text in {"", "-"} else text

        site = cls(
            college=value("단과대학"), department=value("학부/학과"),
            major=value("전공"), homepage=value("홈페이지"),
            compatibility=value("기존 크롤링 적용 가능 여부"),
            notes=value("기타"), dataset=value("약칭"),
        )
        if site.homepage:
            parsed = urlsplit(site.homepage)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError(f"invalid department homepage: {site.homepage!r}")
        if site.dataset and not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", site.dataset):
            raise ValueError(f"invalid department dataset alias: {site.dataset!r}")
        return site

    @property
    def site_key(self) -> str | None:
        if not self.homepage:
            return None
        parsed = urlsplit(self.homepage)
        host = (parsed.hostname or "").lower()
        path = parsed.path.strip("/").lower()
        return host if not path else f"{host}/{path}"


@dataclass(frozen=True)
class SectionConfig:
    id: str
    name: str
    category: str
    kind: str
    path: str
    document_type: str
    bbs_id: str | None = None
    enabled: bool = True
    source_type: str | None = None

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "SectionConfig":
        section = cls(
            id=str(payload.get("id") or "").strip(),
            name=str(payload.get("name") or "").strip(),
            category=str(payload.get("category") or "").strip(),
            kind=str(payload.get("kind") or "").strip(),
            path=str(payload.get("path") or "").strip(),
            document_type=str(payload.get("document_type") or "").strip(),
            source_type=str(payload["source_type"]).strip() if payload.get("source_type") else None,
            bbs_id=str(payload["bbs_id"]).strip() if payload.get("bbs_id") else None,
            enabled=bool(payload.get("enabled", True)),
        )
        section.validate()
        return section

    def validate(self) -> None:
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", self.id):
            raise ValueError(f"invalid section id: {self.id!r}")
        if not self.name or not self.category:
            raise ValueError(f"section {self.id!r} requires name and category")
        if self.kind not in SECTION_KINDS:
            raise ValueError(f"unsupported section kind: {self.kind!r}")
        if self.document_type not in SECTION_DOCUMENT_TYPES:
            raise ValueError(f"unsupported section document_type: {self.document_type!r}")
        if not self.path.startswith("/"):
            raise ValueError(f"section {self.id!r} path must start with '/'")

    def runtime_dict(self, base_url: str) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "category": self.category,
            "url": urljoin(base_url.rstrip("/") + "/", self.path),
            "bbs_id": self.bbs_id,
            "type": self.document_type,
            "source_type": self.source_type,
            "is_board": self.kind == "board",
        }


@dataclass(frozen=True)
class DepartmentConfig:
    dataset: str
    name: str
    base_url: str
    site_prefix: str
    sections: tuple[SectionConfig, ...]
    college: str | None = None
    department: str | None = None
    majors: tuple[str, ...] = ()
    compatibility: str | None = None
    enabled: bool = True
    request_delay_seconds: float = 0.8
    list_delay_seconds: float = 0.5
    request_timeout_seconds: int = 20
    crawl_all_board_pages: bool = True
    reuse_existing_attachments: bool = True
    initial_max_pages: int = 10
    incremental_max_pages: int = 3
    legacy_state_files: tuple[str, ...] = ()
    source_catalog_key: str | None = None
    tls_system_trust_fallback: bool = False
    adapter: str = "numeric_cms"

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "DepartmentConfig":
        sections = tuple(SectionConfig.from_dict(item) for item in payload.get("sections") or [])
        config = cls(
            dataset=str(payload.get("dataset") or "").strip(),
            name=str(payload.get("name") or "").strip(),
            base_url=str(payload.get("base_url") or "").strip().rstrip("/"),
            site_prefix=str(payload.get("site_prefix") or "").strip().strip("/"),
            sections=sections,
            college=str(payload["college"]).strip() if payload.get("college") else None,
            department=str(payload["department"]).strip() if payload.get("department") else None,
            majors=tuple(str(item).strip() for item in payload.get("majors") or [] if str(item).strip()),
            compatibility=str(payload["compatibility"]).strip() if payload.get("compatibility") else None,
            enabled=bool(payload.get("enabled", True)),
            request_delay_seconds=float(payload.get("request_delay_seconds", 0.8)),
            list_delay_seconds=float(payload.get("list_delay_seconds", 0.5)),
            request_timeout_seconds=int(payload.get("request_timeout_seconds", 20)),
            crawl_all_board_pages=bool(payload.get("crawl_all_board_pages", True)),
            reuse_existing_attachments=bool(payload.get("reuse_existing_attachments", True)),
            initial_max_pages=int(payload.get("initial_max_pages", 10)),
            incremental_max_pages=int(payload.get("incremental_max_pages", 3)),
            legacy_state_files=tuple(str(item) for item in payload.get("legacy_state_files") or []),
            source_catalog_key=str(payload["source_catalog_key"]).strip() if payload.get("source_catalog_key") else None,
            tls_system_trust_fallback=bool(payload.get("tls_system_trust_fallback", False)),
            adapter=str(payload.get("adapter") or "numeric_cms").strip(),
        )
        config.validate()
        return config

    def validate(self) -> None:
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", self.dataset):
            raise ValueError(f"invalid dataset: {self.dataset!r}")
        if not self.name or not self.site_prefix:
            raise ValueError(f"department {self.dataset!r} requires name and site_prefix")
        parsed = urlsplit(self.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError(f"department {self.dataset!r} requires an absolute HTTP(S) base_url")
        if self.enabled and not self.sections:
            raise ValueError(f"enabled department {self.dataset!r} requires at least one section")
        if self.adapter not in CMS_ADAPTERS:
            raise ValueError(f"unsupported department CMS adapter: {self.adapter!r}")
        if self.adapter == "numeric_cms":
            missing = [section.id for section in self.sections if section.kind == "board" and not section.bbs_id]
            if missing:
                raise ValueError(f"numeric_cms board section {missing[0]!r} requires bbs_id")
        ids = [section.id for section in self.sections]
        if len(ids) != len(set(ids)):
            raise ValueError(f"department {self.dataset!r} has duplicate section ids")
        paths = [section.path for section in self.sections]
        if len(paths) != len(set(paths)):
            raise ValueError(f"department {self.dataset!r} has duplicate section paths")
        board_ids = [section.bbs_id for section in self.sections if section.kind == "board" and section.bbs_id]
        if len(board_ids) != len(set(board_ids)):
            raise ValueError(f"department {self.dataset!r} has duplicate board identifiers")
        unclassified = [section.id for section in self.sections if section.category == "미분류"]
        if unclassified:
            raise ValueError(f"department {self.dataset!r} has unclassified section {unclassified[0]!r}")
        if min(self.request_delay_seconds, self.list_delay_seconds) < 0:
            raise ValueError("request delays must not be negative")
        if min(self.request_timeout_seconds, self.initial_max_pages, self.incremental_max_pages) <= 0:
            raise ValueError("timeouts and page limits must be positive")

    @property
    def active_sections(self) -> tuple[SectionConfig, ...]:
        return tuple(section for section in self.sections if section.enabled)

    @property
    def crawl_ready(self) -> bool:
        return self.enabled and bool(self.active_sections)


def load_registry(path: Path = DEFAULT_REGISTRY_PATH) -> dict[str, DepartmentConfig]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != REGISTRY_SCHEMA_VERSION:
        raise ValueError(f"unsupported department registry schema: {payload.get('schema_version')!r}")
    configs = [DepartmentConfig.from_dict(item) for item in payload.get("departments") or []]
    registry = {config.dataset: config for config in configs}
    if len(registry) != len(configs):
        raise ValueError("department registry contains duplicate datasets")
    return registry


def load_site_catalog(path: Path = DEFAULT_SITE_CATALOG_PATH) -> list[DepartmentSite]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        expected = {
            "단과대학", "학부/학과", "전공", "홈페이지",
            "기존 크롤링 적용 가능 여부", "약칭",
        }
        if not expected.issubset(set(reader.fieldnames or [])):
            raise ValueError("department site catalog has unexpected columns")
        return [DepartmentSite.from_row(row) for row in reader]


def validate_registry_catalog_links(
    registry: dict[str, DepartmentConfig], sites: list[DepartmentSite]
) -> None:
    sites_by_key = {site.site_key: site for site in sites if site.site_key}
    for config in registry.values():
        if config.source_catalog_key and config.source_catalog_key not in sites_by_key:
            raise ValueError(
                f"department {config.dataset!r} source_catalog_key is missing from sites.csv: "
                f"{config.source_catalog_key!r}"
            )
        if config.source_catalog_key:
            catalog_dataset = sites_by_key[config.source_catalog_key].dataset
            if catalog_dataset and catalog_dataset != config.dataset:
                raise ValueError(
                    f"department {config.dataset!r} does not match catalog dataset "
                    f"{catalog_dataset!r} for {config.source_catalog_key!r}"
                )


def validate_catalog(
    sites: list[DepartmentSite], registry: dict[str, DepartmentConfig]
) -> dict[str, Any]:
    """Return a stable validation report without treating known duplicates as fatal."""
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    keys = [site.site_key for site in sites if site.site_key]
    counts = Counter(keys)
    datasets = [site.dataset for site in sites if site.dataset]
    dataset_counts = Counter(datasets)
    dataset_site_keys: dict[str, set[str | None]] = {}
    for site in sites:
        if site.dataset:
            dataset_site_keys.setdefault(site.dataset, set()).add(site.site_key)
    conflicting_datasets = {
        dataset: site_keys for dataset, site_keys in dataset_site_keys.items()
        if len(site_keys) > 1
    }
    duplicate_keys = sorted(
        key for key, count in counts.items()
        if count > 1 and len({site.dataset for site in sites if site.site_key == key}) > 1
    )
    missing_homepages = [index for index, site in enumerate(sites, start=2) if not site.homepage]
    for key in duplicate_keys:
        warnings.append({
            "code": "DUPLICATE_HOMEPAGE", "site_key": key, "count": counts[key],
        })
    for row_number in missing_homepages:
        warnings.append({"code": "MISSING_HOMEPAGE", "row": row_number})
    for dataset in sorted(conflicting_datasets):
        warnings.append({
            "code": "DUPLICATE_DATASET_ALIAS", "dataset": dataset,
            "count": dataset_counts[dataset],
            "site_keys": sorted(key for key in conflicting_datasets[dataset] if key),
        })
    try:
        validate_registry_catalog_links(registry, sites)
    except ValueError as exc:
        errors.append({"code": "INVALID_REGISTRY_CATALOG_LINK", "message": str(exc)})
    for dataset, config in registry.items():
        base_key = DepartmentSite(None, None, None, config.base_url, None, None).site_key
        if config.source_catalog_key and base_key != config.source_catalog_key:
            errors.append({
                "code": "REGISTRY_BASE_URL_MISMATCH", "dataset": dataset,
                "source_catalog_key": config.source_catalog_key, "base_url_key": base_key,
            })
    return {
        "schema_version": REGISTRY_SCHEMA_VERSION,
        "status": "valid" if not errors else "invalid",
        "stats": {
            "rows": len(sites), "homepages": len(keys), "unique_homepages": len(counts),
            "missing_homepages": len(missing_homepages), "duplicate_homepages": len(duplicate_keys),
            "datasets": len(datasets), "unique_datasets": len(dataset_counts),
            "duplicate_datasets": len(conflicting_datasets),
            "registered_datasets": len(registry),
            "crawl_ready_datasets": sum(config.crawl_ready for config in registry.values()),
        },
        "errors": errors,
        "warnings": warnings,
    }


def find_sites(site_key: str, sites: list[DepartmentSite]) -> list[DepartmentSite]:
    matches = [site for site in sites if site.site_key == site_key]
    if not matches:
        raise KeyError(f"unknown department site_key: {site_key}")
    return matches


def get_department(
    dataset: str,
    path: Path = DEFAULT_REGISTRY_PATH,
    site_catalog_path: Path = DEFAULT_SITE_CATALOG_PATH,
) -> DepartmentConfig:
    registry = load_registry(path)
    validate_registry_catalog_links(registry, load_site_catalog(site_catalog_path))
    try:
        config = registry[dataset]
    except KeyError as exc:
        raise KeyError(f"unknown department dataset: {dataset}") from exc
    if not config.enabled:
        raise ValueError(f"department dataset is disabled: {dataset}")
    if not config.active_sections:
        raise ValueError(f"department dataset has no enabled sections: {dataset}")
    return config

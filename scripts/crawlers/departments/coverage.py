"""Account for every catalog row, including blocked and shared homepages."""
from collections import Counter

from scripts.crawlers.departments.config import DepartmentConfig, DepartmentSite


def build_coverage(registry: dict[str, DepartmentConfig], sites: list[DepartmentSite]) -> dict:
    rows = []
    for row_number, site in enumerate(sites, 2):
        config = registry.get(site.dataset)
        covered_by = []
        if not site.homepage:
            status, reason = "missing_homepage", "catalog has no homepage URL"
        elif not config or config.source_catalog_key != site.site_key:
            status, reason = "unregistered", "homepage has no matching registry entry"
        elif config.crawl_ready:
            status, reason = "ready", "included in --all"
        elif config.inactive_reason == "link_hub" and config.covered_by and all(
                alias in registry and registry[alias].crawl_ready for alias in config.covered_by):
            status, reason = "link_hub", "navigation hub; linked major sites are included in --all"
            covered_by = list(config.covered_by)
        elif config.inactive_reason == "access_blocked":
            status, reason = "blocked", "public site denies access; retained for follow-up"
        elif config.inactive_reason == "user_excluded":
            status, reason = "user_excluded", "explicitly excluded; retained in coverage report"
        else:
            status, reason = "unconfigured", "disabled or has no active sections"
        rows.append(dict(row=row_number, college=site.college, department=site.department,
                         major=site.major, dataset=site.dataset, homepage=site.homepage,
                         status=status, reason=reason, covered_by=covered_by))
    counts = dict(Counter(row["status"] for row in rows))
    attention = {"missing_homepage", "unregistered", "unconfigured", "blocked"}
    return {"status": "needs_attention" if any(row["status"] in attention for row in rows) else "accounted",
            "summary": counts, "rows": rows}

"""Configuration-driven crawler for PKNU department CMS sites."""

from scripts.crawlers.departments.config import (
    DepartmentConfig, DepartmentSite, SectionConfig, load_registry, load_site_catalog,
)

__all__ = ["DepartmentConfig", "DepartmentSite", "SectionConfig", "load_registry", "load_site_catalog"]

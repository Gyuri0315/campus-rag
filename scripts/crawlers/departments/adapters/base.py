"""Interface between CMS-specific HTML parsing and the common crawl runtime."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from bs4 import BeautifulSoup


class DepartmentCMSAdapter(ABC):
    name: str
    version: str = "1.0"

    @abstractmethod
    def discover_menus(self, html: str, base_url: str) -> list[dict[str, str]]: ...

    @abstractmethod
    def analyze_section(
        self, *, name: str, page_url: str, html: str, final_url: str | None = None,
    ) -> dict[str, Any]: ...

    @abstractmethod
    def parse_list(self, soup: BeautifulSoup, board_url: str) -> list[dict[str, Any]]: ...

    def list_request(
        self, board_url: str, page: int, bbs_id: str | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """Return the paginated list request while preserving the legacy default."""
        params: dict[str, Any] = {"pageIndex": page}
        if bbs_id:
            params["bbsId"] = bbs_id
        return board_url, params

    @abstractmethod
    def parse_detail(
        self, soup: BeautifulSoup, post_url: str, item: dict[str, Any],
        *, base_url: str, site_prefix: str,
    ) -> dict[str, Any] | None: ...

    @abstractmethod
    def parse_static(self, soup: BeautifulSoup, *, fallback_title: str) -> dict[str, Any]: ...

    @abstractmethod
    def parse_attachments(
        self, content: BeautifulSoup | Any, *, page_url: str,
        base_url: str, site_prefix: str,
    ) -> list[dict[str, str]]: ...

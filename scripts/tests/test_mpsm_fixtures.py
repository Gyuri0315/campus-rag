from __future__ import annotations

import json
import unittest
from pathlib import Path

from bs4 import BeautifulSoup

from scripts.crawlers.departments.adapters import get_adapter
from scripts.crawlers.departments.config import load_registry
from scripts.crawlers.departments.discovery import discover_from_html
from scripts.crawlers.departments.probe import analyze_site_html
from scripts.crawlers.common.schema import (
    apply_common_schema, normalize_attachment, validate_common_document,
)


FIXTURES = Path(__file__).with_name("fixtures") / "departments" / "mpsm"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
BASE_URL = "https://mpsm.pknu.ac.kr/"


class MpsmFixtureTests(unittest.TestCase):
    def test_fixture_records_verified_transport_policy(self) -> None:
        metadata = json.loads((FIXTURES / "metadata.json").read_text(encoding="utf-8"))
        self.assertTrue(metadata["transport"]["https_started_directly"])
        self.assertTrue(metadata["transport"]["system_trust_fallback"])
        self.assertFalse(metadata["transport"]["verify_false_used"])

    def test_query_menu_discovery_is_reusable(self) -> None:
        html = (FIXTURES / "homepage.html").read_text(encoding="utf-8")
        menus = get_adapter("query_view_do").discover_menus(html, BASE_URL)
        self.assertEqual(["194", "303"], [menu["menu_no"] for menu in menus])

    def test_query_menu_fingerprint_is_supported_by_probe(self) -> None:
        html = "<main>content</main>" + "".join(
            f'<a href="/view.do?no={number}">menu</a>' for number in (194, 303, 304)
        )
        result = analyze_site_html(
            site_key="mpsm.pknu.ac.kr", requested_url=BASE_URL,
            final_url=BASE_URL, html=html, adapter_name="query_view_legacy",
        )
        self.assertTrue(result.compatible)
        self.assertEqual("compatible", result.status)

    def test_query_discovery_uses_host_alias_as_site_prefix(self) -> None:
        homepage = (FIXTURES / "homepage.html").read_text(encoding="utf-8")
        menus = get_adapter("query_view_legacy").discover_menus(homepage, BASE_URL)
        section_html = {
            menus[0]["url"]: (FIXTURES / "static.html").read_text(encoding="utf-8"),
            menus[1]["url"]: (FIXTURES / "board.html").read_text(encoding="utf-8"),
        }
        result = discover_from_html(
            site_key="mpsm.pknu.ac.kr",
            base_url=BASE_URL,
            homepage_html=homepage,
            section_html=section_html,
            adapter_name="query_view_legacy",
        )
        self.assertEqual("mpsm", result.site_prefix)

    def test_static_page_uses_existing_content_selector(self) -> None:
        soup = BeautifulSoup((FIXTURES / "static.html").read_text(encoding="utf-8"), "lxml")
        parsed = get_adapter("query_view_do").parse_static(soup, fallback_title="학부안내")
        self.assertEqual("학부안내", parsed["title"])
        self.assertIn("안내 본문", parsed["content"])

    def test_legacy_table_and_album_lists_use_idx(self) -> None:
        adapter = get_adapter("query_view_legacy")
        listing = BeautifulSoup((FIXTURES / "board.html").read_text(encoding="utf-8"), "lxml")
        album = BeautifulSoup((FIXTURES / "album.html").read_text(encoding="utf-8"), "lxml")
        table_items = adapter.parse_list(listing, BASE_URL + "view.do?no=303")
        album_items = adapter.parse_list(album, BASE_URL + "view.do?no=307")
        self.assertEqual("24367", table_items[0]["source_id"])
        self.assertIn("view=view", table_items[0]["post_url"])
        self.assertEqual("24354", album_items[0]["source_id"])

    def test_legacy_pagination_preserves_menu_and_uses_page_index(self) -> None:
        url, params = get_adapter("query_view_legacy").list_request(
            BASE_URL + "view.do?no=303", 2
        )
        self.assertEqual(BASE_URL + "view.do?no=303", url)
        self.assertEqual({"pageIndex": 2}, params)

    def test_legacy_detail_and_downfile_are_parsed(self) -> None:
        adapter = get_adapter("query_view_legacy")
        detail = BeautifulSoup((FIXTURES / "detail.html").read_text(encoding="utf-8"), "lxml")
        parsed = adapter.parse_detail(
            detail, BASE_URL + "view.do?no=303&idx=24367&view=view",
            {"source_id": "24367"}, base_url=BASE_URL, site_prefix="mpsm",
        )
        self.assertEqual("24367", parsed["source_id"])
        self.assertEqual("관리자", parsed["author"])
        self.assertEqual("2026-05-26", parsed["date"])
        self.assertIn("재입학 희망자", parsed["body"])
        attachment_url = parsed["attachments"][0]["url"]
        self.assertTrue(attachment_url.startswith(
            "https://mpsm.pknu.ac.kr/base/portal/bbs/downFile.do?"
        ))
        self.assertIn("boardId=BRD0001", attachment_url)
        self.assertIn("idx=24367", attachment_url)
        self.assertIn("fidx=18643", attachment_url)

        canonical_attachments = [
            normalize_attachment(item, index=index, project_root=PROJECT_ROOT)
            for index, item in enumerate(parsed["attachments"], start=1)
        ]
        document = apply_common_schema(
            {
                **parsed, "content": parsed["body"], "category": "커뮤니티",
                "attachments": canonical_attachments,
            },
            source_dataset="mpsm", source_id=parsed["source_id"],
            source_site=BASE_URL, document_type="notice",
            content_source="query_view_legacy_html", author=parsed["author"],
            published_at=parsed["date"],
        )
        self.assertEqual([], validate_common_document(document))
        self.assertEqual("mpsm:24367", document["id"])
        self.assertTrue(document["published_at"].endswith("+09:00"))

    def test_registry_uses_reviewed_sections_and_restricted_tls_policy(self) -> None:
        config = load_registry()["mpsm"]
        self.assertTrue(config.enabled)
        self.assertTrue(config.tls_system_trust_fallback)
        self.assertEqual("query_view_legacy", config.adapter)
        self.assertEqual(30, len(config.sections))
        self.assertEqual(25, sum(section.kind == "static_page" for section in config.sections))
        self.assertEqual(5, sum(section.kind == "board" for section in config.sections))


if __name__ == "__main__":
    unittest.main()

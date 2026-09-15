from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from bs4 import BeautifulSoup

from scripts.crawlers.common.storage import get_dataset_paths
from scripts.crawlers.departments import engine
from scripts.crawlers.departments.config import get_department


WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_ROOT = Path(__file__).with_name("fixtures") / "departments" / "numeric_cms"


class FakeResponse:
    def __init__(self, text: str) -> None:
        self.text = text


class DepartmentNumericCmsRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.expected = json.loads((FIXTURE_ROOT / "golden_expected.json").read_text(encoding="utf-8"))
        cls.list_html = (FIXTURE_ROOT / "list.html").read_text(encoding="utf-8")
        cls.detail_html = (FIXTURE_ROOT / "detail.html").read_text(encoding="utf-8")
        cls.static_html = (FIXTURE_ROOT / "static.html").read_text(encoding="utf-8")
        engine.configure_department(get_department("ce"))
        engine.log.disabled = True

    @classmethod
    def tearDownClass(cls) -> None:
        engine.log.disabled = False

    def test_reference_dataset_sections_match_golden_contract(self) -> None:
        self.assertEqual(self.expected["section_count"], len(engine.SECTIONS))
        self.assertEqual(
            self.expected["board_count"], sum(section["is_board"] for section in engine.SECTIONS)
        )
        self.assertEqual(
            self.expected["static_page_count"],
            sum(not section["is_board"] for section in engine.SECTIONS),
        )
        first = engine.SECTIONS[0]
        self.assertEqual(
            self.expected["first_section"],
            {key: first[key] for key in self.expected["first_section"]},
        )

    def test_legacy_section_types_are_standardized_and_preserved(self) -> None:
        sections = {section["id"]: section for section in engine.SECTIONS}
        self.assertEqual("notice", sections["resources"]["type"])
        self.assertEqual("resource", sections["resources"]["source_type"])
        for section_id in (
            "curriculum", "computer_curriculum", "ai_curriculum",
            "module_curriculum", "graduation_requirements",
        ):
            self.assertEqual("static_page", sections[section_id]["type"])
            self.assertEqual("curriculum", sections[section_id]["source_type"])

    def test_curriculum_output_keeps_source_type_metadata(self) -> None:
        captured: list[dict] = []
        section = next(item for item in engine.SECTIONS if item["id"] == "curriculum")
        with (
            patch.object(engine, "fetch", return_value=FakeResponse(self.static_html)),
            patch.object(engine, "save_document", side_effect=lambda doc, _html: captured.append(doc)),
        ):
            stats = engine.crawl_static(object(), section)
        self.assertEqual(0, stats.failed)
        self.assertEqual("static_page", captured[0]["type"])
        self.assertEqual("curriculum", captured[0]["metadata"]["source_type"])

    def test_list_parser_matches_numeric_cms_golden_result(self) -> None:
        items = engine.parse_list_page(
            BeautifulSoup(self.list_html, "lxml"), self.expected["first_section"]["url"]
        )
        self.assertEqual([self.expected["list_item"]], items)

    def test_detail_parser_matches_numeric_cms_golden_result(self) -> None:
        item = self.expected["list_item"]
        detail = engine.parse_view_page(
            BeautifulSoup(self.detail_html, "lxml"), item["post_url"], item
        )
        self.assertIsNotNone(detail)
        detail.pop("slug", None)
        self.assertEqual(self.expected["detail"], detail)

    def test_full_board_result_matches_legacy_document_contract(self) -> None:
        captured: list[dict] = []

        def fake_fetch(_session, _url, **kwargs):
            return FakeResponse(self.list_html if kwargs.get("params") else self.detail_html)

        def capture_document(doc: dict, _raw_html: str) -> None:
            captured.append(doc)

        temp_paths = get_dataset_paths(WORKSPACE_ROOT / ".test-output-do-not-create", "ce")
        with (
            patch.object(engine, "PATHS", temp_paths),
            patch.object(engine, "OUTPUT_JSON", temp_paths.json),
            patch.object(engine, "OUTPUT_HTML", temp_paths.html),
            patch.object(engine, "OUTPUT_FILES", temp_paths.files),
            patch.object(engine, "fetch", side_effect=fake_fetch),
            patch.object(engine, "load_existing_attachments", return_value=[]),
            patch.object(engine, "save_attachments", return_value=[]),
            patch.object(engine, "save_document", side_effect=capture_document),
            patch.object(engine, "CRAWL_ALL_BOARD_PAGES", False),
            patch.object(engine, "INITIAL_MAX_PAGES", 1),
            patch.object(engine, "REUSE_EXISTING_ATTACHMENTS", False),
        ):
            stats, newest = engine.crawl_board(
                object(), engine.SECTIONS[0], {"items": {}}, is_initial=True
            )

        self.assertEqual(123, newest)
        self.assertEqual(1, stats.discovered)
        self.assertEqual(1, stats.requested)
        self.assertEqual(1, stats.new)
        self.assertEqual(0, stats.failed)
        self.assertEqual(1, len(captured))
        document = captured[0]
        expected = self.expected["document"]
        self.assertEqual(expected, {key: document[key] for key in expected})

    def test_static_page_result_matches_legacy_document_contract(self) -> None:
        captured: list[dict] = []

        temp_paths = get_dataset_paths(WORKSPACE_ROOT / ".test-output-do-not-create", "ce")
        with (
            patch.object(engine, "PATHS", temp_paths),
            patch.object(engine, "OUTPUT_JSON", temp_paths.json),
            patch.object(engine, "OUTPUT_HTML", temp_paths.html),
            patch.object(engine, "OUTPUT_FILES", temp_paths.files),
            patch.object(engine, "fetch", return_value=FakeResponse(self.static_html)),
            patch.object(
                engine, "save_document",
                side_effect=lambda doc, _raw_html: captured.append(doc),
            ),
        ):
            stats = engine.crawl_static(object(), engine.SECTIONS[5])

        self.assertEqual(1, stats.discovered)
        self.assertEqual(1, stats.requested)
        self.assertEqual(1, stats.new)
        self.assertEqual(1, len(captured))
        expected = self.expected["static_document"]
        self.assertEqual(expected, {key: captured[0][key] for key in expected})


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from bs4 import BeautifulSoup

from scripts.crawlers.common.storage import get_dataset_paths
from scripts.crawlers.departments import engine
from scripts.crawlers.departments.adapters import get_adapter
from scripts.crawlers.departments.adapters.numeric_cms import NumericCMSAdapter, extract_body_content
from scripts.crawlers.departments.config import get_department
from scripts.crawlers.departments.discovery import analyze_section_html
from scripts.tests._paths import FIXTURES_ROOT, PROJECT_ROOT


FIXTURES = FIXTURES_ROOT / "departments"


class NumericCMSClassificationTests(unittest.TestCase):
    def _analyze(self, fixture: str, name: str):
        return analyze_section_html(
            name=name,
            page_url="https://department.example.test/dept/1234",
            html=(FIXTURES / fixture).read_text(encoding="utf-8"),
        )

    def test_view_link_collections_are_static_pages(self) -> None:
        cases = (
            ("faculty_page.html", "교수진"),
            ("member_directory.html", "구성원"),
            ("profile_list.html", "프로필"),
            ("static_gallery.html", "갤러리"),
            ("static_link_list.html", "교육과정"),
        )
        for fixture, name in cases:
            with self.subTest(fixture=fixture):
                section = self._analyze(fixture, name)
                self.assertEqual("static_page", section.kind)
                self.assertEqual("guide", section.document_type)
                self.assertEqual("candidate", section.status)
                self.assertIsNone(section.bbs_id)
                self.assertEqual([], section.warnings)

    def test_real_board_without_bbs_id_requires_an_adapter(self) -> None:
        section = self._analyze("board_without_bbs_id.html", "소식")
        self.assertEqual("board", section.kind)
        self.assertEqual("notice", section.document_type)
        self.assertEqual("requires_adapter", section.status)
        self.assertIsNone(section.bbs_id)
        self.assertIn("adapter required", section.warnings[0])

    def test_list_variants_use_action_view_document_ids(self) -> None:
        adapter = get_adapter("numeric_cms")
        cases = (
            ("numeric_cms/list_table_variant.html", ["pinned-A", "9994213"], [None, 270]),
            ("numeric_cms/list_card_variant.html", ["28554", "28343"], [None, None]),
            ("numeric_cms/list_gallery_variant.html", ["9983255", "9983254"], [None, None]),
        )
        for fixture, source_ids, post_numbers in cases:
            with self.subTest(fixture=fixture):
                soup = BeautifulSoup((FIXTURES / fixture).read_text(encoding="utf-8"), "lxml")
                items = adapter.parse_list(soup, "https://department.example.test/dept/100")
                self.assertEqual(source_ids, [item["source_id"] for item in items])
                self.assertEqual(post_numbers, [item["post_no"] for item in items])

    def test_navigation_detail_link_is_not_a_list_item(self) -> None:
        adapter = get_adapter("numeric_cms")
        soup = BeautifulSoup(
            (FIXTURES / "numeric_cms/list_table_variant.html").read_text(encoding="utf-8"),
            "lxml",
        )
        items = adapter.parse_list(soup, "https://department.example.test/dept/100")
        self.assertNotIn("9990000", {item["source_id"] for item in items})


class NumericCMSTableTests(unittest.TestCase):
    def test_saved_ce_curriculum_fixture(self):
        fixture = FIXTURES_ROOT / 'departments/numeric_cms/ce_ai_curriculum.html'
        soup = BeautifulSoup(fixture.read_text(encoding='utf-8'), 'lxml')
        parsed = NumericCMSAdapter().parse_static(soup, fallback_title='AI')
        self.assertGreater(len(parsed['content']), 1000)
        self.assertIn('2026', parsed['content'])
        self.assertIn(' | ', parsed['content'])
        self.assertGreater(len(parsed['content'].splitlines()), 10)

    def test_static_table_only_guide(self):
        soup = BeautifulSoup('''<main><table><caption>교육과정</caption>
          <tr><th>학년</th><th>과목</th><th>학점</th></tr>
          <tr><td rowspan="2">1학년</td><td>프로그래밍</td><td>3</td></tr>
          <tr><td>수학</td><td>2</td></tr></table></main>''', 'lxml')
        result = NumericCMSAdapter().parse_static(soup, fallback_title='교육과정')
        self.assertIn('학년 | 과목 | 학점', result['content'])
        self.assertIn('1학년 | 프로그래밍 | 3\n1학년 | 수학 | 2', result['content'])
        self.assertIn('교육과정', result['content'])

    def test_detail_keeps_tables_but_excludes_metadata_and_navigation(self):
        soup = BeautifulSoup('''<div class="a_bdCont"><table>
          <tr><th>작성자</th><td>관리자</td></tr>
          <tr><td class="bdvEdit">신청 안내<table>
            <tr><td>기간</td><td>9월</td></tr></table>문의 바랍니다</td></tr>
          </table><div class="c_bdvNav">이전 글</div></div>''', 'lxml')
        text = extract_body_content(soup)
        self.assertIn('기간 | 9월', text)
        self.assertIn('신청 안내', text)
        self.assertIn('문의 바랍니다', text)
        self.assertNotIn('관리자', text)
        self.assertNotIn('이전 글', text)

    def test_fallback_preserves_multiple_tables_and_colspan(self):
        soup = BeautifulSoup('''<main>안내<table><tr><td colspan="2">공통</td>
          <td>선택</td></tr><tr><td>A</td><td></td><td>B</td></tr></table>
          <table><tr><td>C</td><td>D</td></tr></table><script>hidden()</script></main>''', 'lxml')
        text = extract_body_content(soup)
        self.assertIn('공통 | 공통 | 선택', text)
        self.assertIn('A | | B', text)
        self.assertIn('C | D', text)
        self.assertNotIn('hidden', text)

    def test_nested_table_content_is_not_duplicated(self):
        soup = BeautifulSoup('<main><table><tr><td>외부<table><tr><td>내부</td></tr></table></td></tr></table></main>', 'lxml')
        self.assertEqual(extract_body_content(soup).count('내부'), 1)

    def test_empty_layout_container_before_real_body(self):
        soup = BeautifulSoup('<div class="container"></div><div id="sbCont"><table><tr><td>AI</td><td>3</td></tr></table></div>', 'lxml')
        self.assertEqual(NumericCMSAdapter().parse_static(soup, fallback_title='AI')['content'], 'AI | 3')

    def test_empty_body_states(self):
        from scripts.crawlers.departments.engine import validate_body_content, CrawlDiagnostics
        for state, status, failed in [('empty', 'failed', True), ('image_only', 'partial_success', False), ('attachment_only', 'success', False)]:
            with self.subTest(state=state):
                doc = {'content': '', 'metadata': {}, 'crawl': {'warnings': []}, 'source_id': 'test', 'url': 'https://example.test/page'}
                diagnostics = CrawlDiagnostics()
                self.assertEqual(validate_body_content(doc, {'content_state': state}, diagnostics), failed)
                self.assertEqual(doc['crawl']['status'], status)
                self.assertTrue(doc['crawl']['warnings'])
                self.assertEqual(bool(diagnostics.errors), state != 'attachment_only')

    def test_image_only_static(self):
        parsed = NumericCMSAdapter().parse_static(BeautifulSoup('<div class="container"></div><div id="sbCont"><h3 id="subTitle">guide</h3><img src="/guide.png"></div>', 'lxml'), fallback_title='guide')
        self.assertEqual(parsed['content_state'], 'image_only')

    def test_attachment_only_detail(self):
        parsed = NumericCMSAdapter().parse_detail(BeautifulSoup('<div class="bdvTitle">title</div><div class="a_bdCont"><div class="bdvEdit"></div><a href="/download/a.pdf">a.pdf</a></div>', 'lxml'), 'https://example.test/1', {}, base_url='https://example.test', site_prefix='ce')
        self.assertEqual(parsed['content_state'], 'attachment_only')


WORKSPACE_ROOT = PROJECT_ROOT


FIXTURE_ROOT = FIXTURES_ROOT / "departments" / "numeric_cms"


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
            BeautifulSoup(self.list_html, "lxml"),
            self.expected["list_item"]["post_url"].split("?", 1)[0],
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
            board = next(
                section for section in engine.SECTIONS
                if section["url"] == self.expected["list_item"]["post_url"].split("?", 1)[0]
            )
            stats, newest = engine.crawl_board(object(), board, {"items": {}}, is_initial=True)

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
            static_page = next(
                section for section in engine.SECTIONS
                if section["url"] == self.expected["static_document"]["url"]
            )
            stats = engine.crawl_static(object(), static_page)

        self.assertEqual(1, stats.discovered)
        self.assertEqual(1, stats.requested)
        self.assertEqual(1, stats.new)
        self.assertEqual(1, len(captured))
        expected = self.expected["static_document"]
        self.assertEqual(expected, {key: captured[0][key] for key in expected})


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest

from bs4 import BeautifulSoup

from scripts.crawlers.common.main_static import extract_structure, linked_file_ids, parse_flow, parse_table
from scripts.crawlers.pknu_student_life import parse_board_intro_html, parse_static_page_html


class MainStaticStructureTests(unittest.TestCase):
    def test_table_keeps_headers_coordinates_and_shared_cell(self) -> None:
        soup = BeautifulSoup("""<table><tr><th>Category</th><th>Visit</th><th>Online</th></tr>
          <tr><th>Application</th><td>Office</td><td>Portal</td></tr>
          <tr><th>Review</th><td colspan="2">Shared approval</td></tr></table>""", "lxml")
        table = parse_table(soup.select_one("table"))
        self.assertEqual((table["row_count"], table["column_count"]), (3, 3))
        self.assertEqual(table["rows"][2][1]["column"], 1)
        self.assertEqual(table["rows"][2][1]["colspan"], 2)
        self.assertTrue(table["rows"][0][1]["header"])

    def test_both_diagram_layouts_keep_card_order(self) -> None:
        for layout, card in (("daStep", "dasCont"), ("stFlw_B", "stfCont_B")):
            soup = BeautifulSoup(f"""<div class="{layout}">
              <div class="{card}"><h5>Apply</h5><p>Student</p></div>
              <div class="{card}"><h5>Approve</h5><p>Department</p></div></div>""", "lxml")
            flow = parse_flow(soup.select_one(f".{layout}"))
            self.assertEqual([step["heading"] for step in flow["steps"]], ["Apply", "Approve"])
            self.assertEqual(flow["transitions"], [{"from_order": 1, "to_order": 2}])

    def test_static_parser_finds_structure_and_verified_file_tab(self) -> None:
        html = """<html><title>Graduation guide</title><div id="subCont">
          <ul class="subTab"><li><a href="/main/238">Graduation requirements PDF</a></li></ul>
          <div class="content_wrap"><h4 class="subName">Requirements</h4>
          <p>Students must check their required credits and apply through the academic portal.
          The department reviews the application and the college confirms the result.
          Please consult the linked document for detailed graduation requirements.</p>
          <table><tr><th>Credits</th><td>130 or more</td></tr></table>
          <dl><dd><div class="stFlw_B">
            <div class="stfCont_B"><h5>Apply</h5><p>Student</p></div>
            <div class="stfCont_B"><h5>Approve</h5><p>Department</p></div>
          </div></dd></dl></div></div></html>"""
        parsed = parse_static_page_html(html, 94)
        self.assertEqual(parsed.status, "ready")
        self.assertEqual(parsed.linked_file_page_ids, (238,))
        self.assertEqual(parsed.structure["table_count"], 1)
        self.assertEqual(parsed.structure["flow_count"], 1)
        self.assertIn("130 or more", parsed.content)

    def test_empty_decorative_table_does_not_fail_page(self) -> None:
        soup = BeautifulSoup("""<div id="subCont"><div class="content_wrap">
          <h4 class="subName">Guide</h4><table><tr></tr></table>
          <p>This is a sufficiently long academic guide paragraph for students.</p>
          </div></div>""", "lxml")
        structure = extract_structure(soup.select_one("#subCont"))
        self.assertEqual(structure["table_count"], 0)
        self.assertIn("academic guide", structure["content"])

    def test_table_inside_list_item_keeps_grid(self) -> None:
        soup = BeautifulSoup("""<div id="subCont"><div class="content_wrap">
          <h4 class="subName">Grades</h4><ul><li>Grade scale
          <div class="subTb_scroll"><table><tr><th>Grade</th><th>Point</th></tr>
          <tr><td>A+</td><td>4.50</td></tr></table></div></li></ul>
          </div></div>""", "lxml")
        structure = extract_structure(soup.select_one("#subCont"))
        self.assertEqual(structure["table_count"], 1)
        item = structure["sections"][0]["blocks"][0]["items"][0]
        table = next(block for block in item["blocks"] if block["type"] == "table")
        self.assertEqual((table["row_count"], table["column_count"]), (2, 2))
        self.assertIn("A+ / 4.50", structure["content"])

    def test_file_discovery_rejects_unknown_or_external_links(self) -> None:
        soup = BeautifulSoup("""<div class="subTab">
          <a href="/main/238">file</a><a href="/main/999">unknown</a>
          <a href="https://elsewhere.example/main/238">external</a></div>""", "lxml")
        self.assertEqual(linked_file_ids(soup, "https://www.pknu.ac.kr/main/94", {238}), (238,))

    def test_board_intro_keeps_guide_without_list_rows(self) -> None:
        html = """<html><title>Credit transfer</title><div id="subCont">
          <h4 class="subName">Credit transfer</h4>
          <h4 class="subNameH4">Eligibility</h4>
          <p>Students may apply after completing one semester at this university.
          Applications need approval from their department and college.</p>
          <form id="frmPost"><input name="bbsId" value="307"></form>
          <div class="bdCont">Board controls</div>
          <table class="brdList"><tr><td><a href="?action=view&amp;no=123">Post title</a></td></tr></table>
          <div class="paging">1 / 38</div></div></html>"""
        parsed = parse_board_intro_html(html, 95)
        self.assertEqual(parsed.status, "ready")
        self.assertIn("Eligibility", parsed.content)
        self.assertNotIn("Post title", parsed.content)
        self.assertNotIn("Board controls", parsed.content)


if __name__ == "__main__":
    unittest.main()

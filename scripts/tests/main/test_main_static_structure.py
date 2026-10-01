from __future__ import annotations

import unittest

from bs4 import BeautifulSoup

from scripts.crawlers.common.main_static import extract_structure, linked_file_ids, parse_flow, parse_table
from scripts.crawlers.pknu_student_life import parse_board_intro_html, parse_static_page_html


class MainStaticStructureTests(unittest.TestCase):
    def test_residence_hall_page_keeps_body_without_shortcut_or_pdf(self) -> None:
        html = """<html><title>Residence hall</title><div id="subCont">
          <div class="content_wrap"><h4 class="subNameH4">Residence hall</h4>
          <div class="sblTxt"><a href="https://dormitory.pknu.ac.kr/">Homepage shortcut</a></div>
          <p>Students can live in the residence hall and use its facilities.
          The university provides space for students on both campuses.</p>
          <img src="/upload/intro.jpg" alt="Welcome">
          <table><tr><th>Hall</th><th>Capacity</th><th>Photo</th></tr>
          <tr><td>Sejong</td><td>850</td>
          <td><img src="https://devwww.pknu.ac.kr/upload/missing.jpg"></td></tr></table>
          <p>See the hall homepage for details.
          <a href="https://dormitory.pknu.ac.kr/">Homepage link</a></p>
          <ul><li>Penalty guide PDF <a href="#none">View</a></li>
          <div class="uploadPdf" data-id="7780"></div></ul>
          </div></div></html>"""
        parsed = parse_static_page_html(html, 262)
        self.assertEqual(parsed.status, "ready")
        self.assertEqual(parsed.structure["table_count"], 1)
        self.assertIn("Sejong", parsed.content)
        self.assertNotIn("Homepage shortcut", parsed.content)
        self.assertNotIn("Homepage link", parsed.content)
        self.assertNotIn("Penalty guide", parsed.content)
        self.assertFalse(parsed.attachments)
        self.assertFalse(parsed.embedded_pdfs)
        self.assertFalse(parsed.reference_links)
        self.assertEqual([item["src"] for item in parsed.body_images], ["/upload/intro.jpg"])
        self.assertEqual(parsed.warnings, ("unavailable_source_images:1",))

    def test_student_organization_chart_and_homepage_links(self) -> None:
        html = """<html><title>Student groups</title><div id="subCont">
          <div class="content_wrap"><h4 class="subNameH4">Organization</h4>
          <p>Student groups plan activities and represent enrolled students.</p>
          <div class="orgCont"><h3>Student council</h3><h4>President</h4>
          <h5>Vice president</h5><h6>Executive chair</h6>
          <ul><li>Welfare office</li><li>Culture office</li></ul></div>
          <h4 class="subNameH4">Homepages</h4><table>
          <tr><th>Council</th><td><a href="https://example.org/council">Council homepage</a></td></tr>
          <tr><th>Clubs</th><td><a href="https://example.org/clubs">Clubs homepage</a></td></tr>
          </table></div></div></html>"""
        parsed = parse_static_page_html(html, 263)
        self.assertEqual(parsed.status, "ready")
        self.assertEqual(parsed.structure["organization_chart_count"], 1)
        self.assertEqual(parsed.structure["table_count"], 1)
        chart = parsed.structure["sections"][0]["blocks"][1]
        self.assertEqual(chart["type"], "organization_chart")
        self.assertEqual([node["parent_order"] for node in chart["nodes"]],
                         [None, 1, 2, 3, 4, 4])
        self.assertEqual([link["context"] for link in parsed.reference_links],
                         [["Council"], ["Clubs"]])
        self.assertEqual([link["url"] for link in parsed.reference_links],
                         ["https://example.org/council", "https://example.org/clubs"])
        self.assertFalse(parsed.attachments)

    def test_accident_files_include_download_endpoint_and_zip(self) -> None:
        links = "".join(
            f'<li><a download="Case {index}" href="{href}">Case {index}</a></li>'
            for index, href in enumerate((
                "/mdaDownload.do?no=400", "/upload/2021.pdf",
                "/upload/2022.pdf", "/upload/2023.pdf", "/upload/2024.zip",
            ), 1)
        )
        html = ("<html><title>Accident cover</title><div id='subCont'>"
                "<div class='content_wrap'><h4 class='subNameH4'>Required files</h4>"
                "<p>Use the forms that match the time when the accident occurred. "
                "The school reviews each claim and its supporting documents.</p>"
                f"<ul>{links}</ul></div></div></html>")
        parsed = parse_static_page_html(html, 260)
        self.assertEqual(parsed.status, "ready")
        self.assertEqual(len(parsed.attachments), 5)
        self.assertEqual(parsed.attachments[0]["url"],
                         "https://www.pknu.ac.kr/mdaDownload.do?no=400")
        self.assertTrue(parsed.attachments[-1]["url"].endswith("2024.zip"))

    def test_static_parser_keeps_internal_downloads(self) -> None:
        html = """<html><title>Student ID</title><div id="subCont">
          <div class="content_wrap"><h4>Issuance</h4>
          <p>Students can apply through the portal or visit the bank office.
          Each method has a separate instruction document for the process.</p>
          <a download="Issuance process" href="/upload/process.pptx">Process</a>
          <a href="https://www.pknu.ac.kr/upload/guide.hwp">Guide</a>
          <a href="https://elsewhere.example/upload/other.pdf">Other site</a>
          </div></div></html>"""
        parsed = parse_static_page_html(html, 114)
        self.assertEqual(parsed.status, "ready")
        self.assertEqual([item["url"] for item in parsed.attachments], [
            "https://www.pknu.ac.kr/upload/process.pptx",
            "https://www.pknu.ac.kr/upload/guide.hwp",
        ])

    def test_nested_student_id_page_excludes_login_shortcut(self) -> None:
        html = """<html><title>International student ID</title>
          <div id="subCont"><ul class="subMenu"><li>Other pages</li></ul>
          <div id="subCont"><div class="col-lg-12"><h4 class="subName">ISIC</h4>
          <p>Students can apply for an international ID after checking eligibility.
          The university verifies enrollment before the student completes payment.</p>
          <img src="/upload/card.jpg" alt="Card">
          <div class="lgnForm"><a href="/main/149">Login shortcut</a></div>
          </div></div></div></html>"""
        parsed = parse_static_page_html(html, 115)
        self.assertEqual(parsed.status, "ready")
        self.assertIn("verifies enrollment", parsed.content)
        self.assertEqual([image["src"] for image in parsed.body_images], ["/upload/card.jpg"])
        self.assertNotIn("Login shortcut", parsed.content)
        self.assertNotIn("Other pages", parsed.content)

    def test_certificate_page_keeps_two_embedded_pdf_ids(self) -> None:
        html = """<html><title>Certificates</title><div id="subCont">
          <div class="content_wrap"><h4 class="subNameH4">Issuance</h4>
          <p>Students can request certificates at the office or through an online
          service. The university also provides two PDF instruction documents.</p>
          <ul><li>Machine location PDF <button>View</button></li>
          <div class="uploadPdf" data-id="5738"></div>
          <li>Password change PDF <button>View</button></li>
          <div class="uploadPdf" data-id="5740"></div></ul>
          </div></div></html>"""
        parsed = parse_static_page_html(html, 449)
        self.assertEqual(parsed.status, "ready")
        self.assertEqual([item["media_id"] for item in parsed.embedded_pdfs],
                         ["5738", "5740"])
        self.assertEqual(parse_static_page_html(html, 448).status, "needs_review")

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

    def test_exchange_program_diagram_keeps_numbered_and_final_steps(self) -> None:
        html = """<html><title>Exchange program</title><div id="subCont">
          <div class="content_wrap"><h4 class="subNameH4">Process</h4>
          <div class="stFlw col4"><div><div class="stfCont"><em>01</em>
          <div><p>Announcement</p></div></div></div>
          <div><div class="stfCont"><em>02</em><div><p>Application</p></div></div></div>
          <div><div class="stfCont last"><div><p>Departure</p></div></div></div>
          </div><p>Students should confirm the host university requirements and
          prepare their travel documents before departure.</p></div></div></html>"""
        parsed = parse_static_page_html(html, 258)
        self.assertEqual(parsed.status, "ready")
        self.assertEqual(parsed.structure["flow_count"], 1)
        flow = parsed.structure["sections"][0]["blocks"][0]
        self.assertEqual(flow["type"], "flow")
        self.assertEqual([step["heading"] for step in flow["steps"]],
                         ["Announcement", "Application", "Departure"])
        self.assertEqual([step["display_number"] for step in flow["steps"]],
                         ["01", "02", None])
        self.assertEqual(flow["transitions"], [
            {"from_order": 1, "to_order": 2}, {"from_order": 2, "to_order": 3}])
        self.assertIn("3. Departure", parsed.content)

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

    def test_static_parser_keeps_diagram_images_after_tables(self) -> None:
        html = """<html><title>Field practice</title><div id="subCont"><div class="content_wrap">
          <h4 class="subName">Field practice</h4>
          <p>Students complete a placement and submit a report to their department.
          The department checks the placement record before granting course credit.</p>
          <table><tr><th>Course</th><td>Placement</td></tr></table>
          <h4 class="subNameH4">Workflow 1</h4><div><a href="/flow1.png"><img src="/flow1.png"></a></div>
          <h4 class="subNameH4">Workflow 2</h4><div><img src="/flow2.jpg"></div>
          </div></div></html>"""
        parsed = parse_static_page_html(html, 101)
        self.assertEqual(parsed.status, "ready")
        self.assertEqual(parsed.structure["table_count"], 1)
        self.assertEqual([image["src"] for image in parsed.body_images],
                         ["/flow1.png", "/flow2.jpg"])
        self.assertEqual(parsed.content.count("[이미지]"), 2)

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

    def test_nested_list_directly_inside_list_keeps_child_items(self) -> None:
        soup = BeautifulSoup("""<div id="subCont"><div class="content_wrap">
          <h4 class="subNameH4">Application period</h4>
          <ul><li>Apply before the deadline</li>
            <ul><li>Late applications are rejected</li><li>Check the form</li></ul>
          </ul></div></div>""", "lxml")
        structure = extract_structure(soup.select_one("#subCont"))
        self.assertIn("Late applications are rejected", structure["content"])
        self.assertIn("Check the form", structure["content"])
        outer = structure["sections"][0]["blocks"][0]
        self.assertEqual(outer["items"][0]["blocks"][1]["type"], "list")

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

import unittest

from scripts.rag.classify_body_image_layout import classify


class BodyImageLayoutClassifierTests(unittest.TestCase):
    def test_existing_table_is_high_confidence_table(self):
        result = classify({"result": {"paragraphs": [], "tables": [{"type": "ocr_table"}]}, "warnings": []}, {
            "dark_ratio": .1, "horizontal_line_groups": 2, "vertical_line_groups": 2,
            "connector_horizontal_line_rows": 0, "connector_vertical_line_rows": 0,
            "wide_horizontal_line_rows": 0, "wide_vertical_line_rows": 0, "closed_grid": True,
        })
        self.assertEqual(result["layout_type"], "table")

    def test_empty_ocr_is_no_text(self):
        result = classify({"status": "empty", "blocks": [], "warnings": []}, {
            "dark_ratio": .01, "horizontal_line_groups": 0, "vertical_line_groups": 0,
            "connector_horizontal_line_rows": 0, "connector_vertical_line_rows": 0,
            "wide_horizontal_line_rows": 0, "wide_vertical_line_rows": 0, "closed_grid": False,
        })
        self.assertEqual(result["layout_type"], "no_text")

    def test_empty_ocr_with_visual_ink_stays_unknown(self):
        result = classify({"status": "empty", "blocks": [], "warnings": []}, {
            "dark_ratio": .12, "horizontal_line_groups": 0, "vertical_line_groups": 0,
            "connector_horizontal_line_rows": 0, "connector_vertical_line_rows": 0,
            "wide_horizontal_line_rows": 0, "wide_vertical_line_rows": 0, "closed_grid": False,
        })
        self.assertEqual(result["layout_type"], "unknown")

    def test_ambiguous_input_stays_unknown(self):
        result = classify({"blocks": [], "warnings": []}, {
            "dark_ratio": .1, "horizontal_line_groups": 0, "vertical_line_groups": 0,
            "connector_horizontal_line_rows": 0, "connector_vertical_line_rows": 0,
            "wide_horizontal_line_rows": 0, "wide_vertical_line_rows": 0, "closed_grid": False,
        })
        self.assertEqual(result["layout_type"], "unknown")

    def test_grid_like_image_without_verified_cells_is_not_guessed_as_diagram(self):
        image = {"blocks": [{"type": "ocr_paragraph", "text": "x", "bbox": [i * 10, i * 10, i * 10 + 5, i * 10 + 5]}
                            for i in range(4)], "width": 100, "height": 100, "warnings": []}
        result = classify(image, {"dark_ratio": .1, "horizontal_line_groups": 6, "vertical_line_groups": 6,
                                  "connector_horizontal_line_rows": 8, "connector_vertical_line_rows": 8,
                                  "wide_horizontal_line_rows": 8, "wide_vertical_line_rows": 8, "closed_grid": False})
        self.assertEqual(result["layout_type"], "unknown")
        self.assertIn("GRID_LIKE_LAYOUT_REQUIRES_REVIEW", result["classification_evidence"])


if __name__ == "__main__":
    unittest.main()

import unittest

from scripts.rag.body_image_layout import enrich_entry, review_template, validate_review


class BodyImageLayoutTests(unittest.TestCase):
    def test_legacy_paragraphs_are_wrapped_as_unknown(self):
        entry = {"source_path": "files/ce/output/json/a.json", "images": [{
            "sha256": "abc", "status": "needs_review", "warnings": ["OCR_REVIEW_REQUIRED"],
            "blocks": [{"type": "ocr_paragraph", "text": "본문"}],
        }]}
        self.assertTrue(enrich_entry(entry))
        image = entry["images"][0]
        self.assertEqual(image["layout_type"], "unknown")
        self.assertEqual(image["extractor"], "paragraph_ocr")
        self.assertEqual(image["result"]["paragraphs"][0]["text"], "본문")

    def test_table_blocks_are_preserved(self):
        entry = {"source_path": "files/ce/output/json/a.json", "images": [{
            "sha256": "abc", "status": "needs_review", "warnings": [],
            "blocks": [{"type": "ocr_table", "rows": []}],
        }]}
        enrich_entry(entry)
        image = entry["images"][0]
        self.assertEqual(image["layout_type"], "table")
        self.assertEqual(image["result"]["tables"][0]["type"], "ocr_table")

    def test_review_template_is_keyed_by_source_and_hash(self):
        entry = {"source_path": "files/ce/output/json/a.json", "images": [{"sha256": "abc"}]}
        review = review_template(entry)
        validate_review(review)
        self.assertEqual(review["images"][0]["review_key"], "files/ce/output/json/a.json#abc")
        self.assertEqual(review["rag_inclusion_policy"]["corrected"], "include")
        self.assertEqual(review["rag_inclusion_policy"]["needs_review"], "exclude")

    def test_corrected_review_requires_structured_correction(self):
        entry = {"source_path": "files/ce/output/json/a.json", "images": [{"sha256": "abc"}]}
        review = review_template(entry)
        review["images"][0]["decision"] = "corrected"
        with self.assertRaisesRegex(ValueError, "correction"):
            validate_review(review)
        review["images"][0]["correction"]["tables"].append({
            "table_index": 0,
            "cells": [{"row": 0, "column": 0, "text": "교정 셀"}],
        })
        validate_review(review)

    def test_corrected_text_is_an_allowed_correction_overlay(self):
        entry = {"source_path": "files/ce/output/json/a.json", "images": [{"sha256": "abc"}]}
        review = review_template(entry)
        review["images"][0]["decision"] = "corrected"
        review["images"][0]["correction"]["corrected_text"] = "교정된 이미지 본문"
        validate_review(review)

    def test_corrected_text_can_be_stored_directly_on_review_item(self):
        entry = {"source_path": "files/ce/output/json/a.json", "images": [{"sha256": "abc"}]}
        review = review_template(entry)
        review["images"][0]["decision"] = "corrected"
        review["images"][0]["corrected_text"] = "교정된 이미지 본문"
        validate_review(review)

    def test_needs_review_is_valid_and_excluded_by_policy(self):
        entry = {"source_path": "files/ce/output/json/a.json", "images": [{"sha256": "abc"}]}
        review = review_template(entry)
        review["images"][0]["decision"] = "needs_review"
        validate_review(review)
        self.assertEqual(review["rag_inclusion_policy"]["needs_review"], "exclude")


if __name__ == "__main__":
    unittest.main()

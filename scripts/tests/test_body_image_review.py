import unittest
from pathlib import Path

from scripts.rag.body_image_review import compile_body_image_blocks
from scripts.rag.vectorization import extract_chunk_records


class BodyImageReviewRagTests(unittest.TestCase):
    def setUp(self):
        self.source_path = "files/ce/output/json/category/post.json"

    def test_missing_review_preserves_only_original_document_blocks(self):
        images = [{
            "sha256": "a" * 64,
            "status": "extracted",
            "layout_type": "prose",
            "extractor": "paragraph_ocr",
            "result": {"paragraphs": [{"text": "must not auto-merge"}], "tables": []},
        }]
        base = [{"type": "body", "text": "original body"}]
        blocks, provenance, warnings = compile_body_image_blocks(
            self.source_path, images, [], base
        )
        self.assertEqual(blocks, base)
        self.assertFalse(warnings)
        self.assertEqual(provenance[0]["review_decision"], None)
        self.assertFalse(provenance[0]["included"])
        self.assertEqual(provenance[0]["exclusion_reason"], "NO_REVIEW_FILE_OR_IMAGE_ENTRY")

    def test_decision_priority_and_corrected_text(self):
        decisions = ["accepted", "corrected", "excluded", "reprocess", "needs_review"]
        images = [{
            "sha256": str(index) * 64,
            "status": "needs_review",
            "layout_type": "prose",
            "extractor": "paragraph_ocr",
            "result": {"paragraphs": [{"text": f"ocr-{decision}"}], "tables": []},
        } for index, decision in enumerate(decisions, start=1)]
        reviews = [{
            "source_path": self.source_path,
            "image_sha256": str(index) * 64,
            "decision": decision,
            "corrected_text": "reviewed correction" if decision == "corrected" else None,
        } for index, decision in enumerate(decisions, start=1)]
        blocks, provenance, warnings = compile_body_image_blocks(
            self.source_path, images, reviews,
            [{"type": "body", "text": "original body"}]
        )
        added = [block["text"] for block in blocks[1:]]
        self.assertEqual(added, ["ocr-accepted", "reviewed correction"])
        self.assertEqual([item["included"] for item in provenance], [True, True, False, False, False])
        self.assertEqual([item["review_decision"] for item in provenance], decisions)
        self.assertFalse(warnings)

    def test_table_structure_is_serialized_with_cell_positions_and_spans(self):
        images = [{
            "sha256": "f" * 64,
            "status": "extracted",
            "layout_type": "table",
            "extractor": "table_ocr",
            "result": {"paragraphs": [], "tables": [{"rows": [{"cells": [
                {"row": 0, "column": 0, "rowspan": 2, "colspan": 1, "text": "학년"},
                {"row": 0, "column": 1, "rowspan": 1, "colspan": 2, "text": "과목"},
            ]}]}]},
        }]
        reviews = [{
            "source_path": self.source_path,
            "image_sha256": "f" * 64,
            "decision": "accepted",
        }]
        blocks, _, _ = compile_body_image_blocks(
            self.source_path, images, reviews, []
        )
        self.assertIn("rowspan=2", blocks[0]["text"])
        self.assertIn("colspan=2", blocks[0]["text"])

    def test_corrected_structure_replaces_ocr_result(self):
        digest = "e" * 64
        images = [{
            "sha256": digest,
            "layout_type": "diagram",
            "extractor": "diagram_ocr",
            "result": {"paragraphs": [{"text": "uncorrected OCR"}], "tables": []},
        }]
        reviews = [{
            "source_path": self.source_path,
            "image_sha256": digest,
            "decision": "corrected",
            "corrected_structure": {"diagram": {"nodes": [
                {"id": "n1", "text": "교정한 노드"},
            ], "edges": [], "unverified_edges": []}},
        }]
        blocks, _, _ = compile_body_image_blocks(self.source_path, images, reviews, [])
        self.assertEqual(len(blocks), 1)
        self.assertIn("교정한 노드", blocks[0]["text"])

    def test_image_provenance_reaches_vectorized_chunk_metadata(self):
        image_provenance = [{
            "source_path": self.source_path,
            "image_sha256": "b" * 64,
            "review_decision": "accepted",
            "extractor": "paragraph_ocr",
            "layout_type": "prose",
            "included": True,
        }]
        document = {
            "slug": "post",
            "source_file": "post.json",
            "provenance": {"doc_title": "Reviewed document"},
            "body_image_provenance": image_provenance,
            "chunks": [{"chunk_id": 1, "text": "Reviewed image text has enough distinct words for retrieval."}],
        }
        records = extract_chunk_records(
            document, Path("files/ce/preprocessed/json/category/post.json"), Path.cwd()
        )
        self.assertEqual(records[0]["metadata"]["body_image_provenance"], image_provenance)


if __name__ == "__main__":
    unittest.main()

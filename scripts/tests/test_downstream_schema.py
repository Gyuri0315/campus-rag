from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.crawlers.common.reader import read_document, remove_redundant_legacy_fields
from scripts.crawlers.common.schema import apply_common_schema, attachment_text, content_sha256
from scripts.migrations.crawler_documents import convert_file
from scripts.rag.file_preprocessing import extract_crawled_json_provenance
from scripts.rag.vectorization import extract_chunk_records
from scripts.rag.load_to_supabase import normalize_vector_metadata
from scripts.rule.preprocessing import extract_rule_json_blocks


WORKSPACE_ROOT = Path(__file__).resolve().parents[2]


def legacy_notice() -> dict:
    return {
        "slug": "legacy-slug", "title": "Legacy notice", "date": "2026-09-02",
        "url": "https://www.pknu.ac.kr/main/163?action=view&no=1",
        "category": "notice", "subcategory": "general", "type": "notice",
        "content": "same document content", "crawled_at": "2026-09-02T10:00:00",
        "attachments": [], "notice_no": "12",
    }


class DownstreamSchemaTests(unittest.TestCase):
    def test_legacy_department_types_map_without_losing_raw_type(self) -> None:
        cases = (("curriculum", "static_page"), ("resource", "notice"))
        for raw_type, expected_type in cases:
            with self.subTest(raw_type=raw_type):
                legacy = {
                    **legacy_notice(),
                    "type": raw_type,
                    "source_dataset": "ce",
                    "source_id": raw_type,
                }
                converted = read_document(
                    legacy, dataset="ce", project_root=WORKSPACE_ROOT,
                    remove_redundant=True,
                )
                self.assertEqual(expected_type, converted["type"])
                self.assertEqual(raw_type, converted["metadata"]["source_type"])
                self.assertEqual(legacy["content"], converted["content"])
                self.assertEqual(legacy["title"], converted["title"])

    def test_legacy_and_schema_1_reader(self) -> None:
        legacy = read_document(
            legacy_notice(), dataset="pknu_notice", project_root=WORKSPACE_ROOT,
            remove_redundant=True,
        )
        current = read_document(legacy, project_root=WORKSPACE_ROOT)
        self.assertEqual("1.0", current["schema_version"])
        self.assertEqual("2026-09-02T00:00:00+09:00", current["published_at"])
        self.assertEqual(12, current["notice_no"])
        self.assertNotIn("date", current)
        self.assertNotIn("crawled_at", current)

    def test_rule_legacy_text_is_consolidated_without_changing_blocks(self) -> None:
        legacy = {
            "slug": "rule", "title": "Rule", "date": "2026-09-02",
            "url": "https://www.pknu.ac.kr/rule/view?no=1", "source_id": "1",
            "source_site": "pknu_rule", "category": "pknu_rule_law",
            "subcategory": "regulation", "type": "gyu",
            "content": "rule body\n\nattachment text", "html_text": "rule body",
            "page_content": "rule body", "crawled_at": "2026-09-02T10:00:00",
            "attachments": [{
                "name": "rule.pdf", "url": "https://example.test/rule.pdf",
                "saved_path": None, "downloaded": False,
            }],
            "attachment_texts": [{"name": "rule.pdf", "text": "attachment text"}],
            "file_preview_texts": [{"name": "rule.pdf", "text": "attachment text"}],
        }
        converted = read_document(
            legacy, dataset="rule", project_root=WORKSPACE_ROOT, remove_redundant=True
        )
        self.assertEqual("regulation", converted["type"])
        self.assertEqual("gyu", converted["metadata"]["source_type"])
        for field in ("date", "crawled_at", "html_text", "page_content", "attachment_texts", "file_preview_texts"):
            self.assertNotIn(field, converted)
        blocks, _ = extract_rule_json_blocks(converted, WORKSPACE_ROOT / "missing")
        texts = [block["text"] for block in blocks]
        self.assertTrue(any("rule body" in text for text in texts))
        self.assertTrue(any("attachment text" in text for text in texts))

    def test_preprocessing_and_vectorization_emit_canonical_metadata(self) -> None:
        with tempfile.TemporaryDirectory(dir=WORKSPACE_ROOT) as directory:
            root = Path(directory)
            source = root / "files" / "ce" / "output" / "json" / "cat" / "doc.json"
            source.parent.mkdir(parents=True)
            source.write_text(json.dumps(legacy_notice()), encoding="utf-8")
            provenance = extract_crawled_json_provenance(source)
            self.assertEqual("ce", provenance["source_dataset"])
            # Explicit path dataset wins when a legacy fixture has no source_dataset.
            self.assertIn("published_at", provenance)
            self.assertNotIn("date", provenance)
            preprocessed = {
                "slug": "doc", "source_file": "doc.json", "source_path": source.as_posix(),
                "source_kind": "post", "provenance": provenance,
                "chunks": [{
                    "chunk_id": "1",
                    "text": "This canonical university notice explains registration schedules, course requirements, scholarship applications, student services, academic policies, deadlines, contacts, and required supporting documents in sufficient detail for retrieval testing.",
                }],
            }
            records = extract_chunk_records(preprocessed, source, WORKSPACE_ROOT)
            metadata = records[0]["metadata"]
            self.assertIn("published_at", metadata)
            self.assertIn("crawl", metadata)
            self.assertNotIn("date", metadata)
            self.assertNotIn("crawled_at", metadata)

    def test_migration_dry_run_preserves_file_content_and_counts(self) -> None:
        with tempfile.TemporaryDirectory(dir=WORKSPACE_ROOT) as directory:
            path = Path(directory) / "legacy.json"
            original = legacy_notice()
            path.write_text(json.dumps(original, ensure_ascii=False), encoding="utf-8")
            before = path.read_bytes()
            report = convert_file(path, "pknu_notice", apply=False)
            self.assertEqual(before, path.read_bytes())
            self.assertTrue(report["content_preserved"])
            self.assertEqual(report["attachments_before"], report["attachments_after"])
            self.assertEqual(content_sha256(original["content"]), report["content_hash_after"])
            self.assertIsNone(report["backup"])

    def test_schema_file_matches_required_model(self) -> None:
        schema = json.loads((WORKSPACE_ROOT / "schemas" / "crawler-document-1.0.schema.json").read_text(encoding="utf-8"))
        required = set(schema["required"])
        fixture = apply_common_schema(
            {"title": "x", "url": "https://example.test/x", "category": "x", "content": "x"},
            source_dataset="ce", source_id="1", source_site="https://example.test",
            document_type="notice", content_source="fixture",
        )
        self.assertTrue(required.issubset(fixture))
        self.assertEqual("1.0", schema["properties"]["schema_version"]["const"])

    def test_loader_normalizes_transition_metadata(self) -> None:
        metadata = normalize_vector_metadata({
            "date": "2026-09-02", "crawled_at": "2026-09-02T10:00:00+09:00",
            "num_chars": "123", "attachments": [{"size_bytes": "45"}],
        })
        self.assertEqual("2026-09-02", metadata["published_at"])
        self.assertEqual(123, metadata["num_chars"])
        self.assertEqual(45, metadata["attachments"][0]["size_bytes"])
        self.assertNotIn("date", metadata)
        self.assertNotIn("crawled_at", metadata)

    def test_information_is_not_removed_when_not_represented(self) -> None:
        doc = {
            "published_at": None, "crawl": {}, "content": "main body",
            "html_text": "unique hidden text", "attachment_texts": [{"text": "unique attachment"}],
            "attachments": [],
        }
        removed = remove_redundant_legacy_fields(doc)
        self.assertNotIn("html_text", removed)
        self.assertNotIn("attachment_texts", removed)
        self.assertIn("html_text", doc)
        self.assertIn("attachment_texts", doc)


if __name__ == "__main__":
    unittest.main()

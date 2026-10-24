from __future__ import annotations

import unittest

from scripts.crawlers.common.schema import (
    apply_common_schema,
    content_sha256,
    document_slug,
    validate_common_document,
)


class CommonCrawlerSchemaTests(unittest.TestCase):
    def build_fixture(
        self,
        *,
        dataset: str,
        source_id: str,
        source_site: str,
        document_type: str,
        date: str | None,
        content: str = "첫 줄  \r\n둘째 줄",
        metadata: dict | None = None,
    ) -> dict:
        legacy = {
            "slug": "legacy-title-dependent",
            "title": "fixture title",
            "date": date or "",
            "url": f"{source_site}/fixture",
            "category": "fixture",
            "subcategory": "fixture-child",
            "type": "legacy_type",
            "content": content,
            "attachments": [],
            "legacy_only": True,
        }
        return apply_common_schema(
            legacy,
            source_dataset=dataset,
            source_id=source_id,
            source_site=source_site,
            document_type=document_type,
            content_source="fixture_parser",
            published_at=date,
            metadata=metadata,
            crawled_at="2026-09-02T10:30:00",
        )

    def test_all_crawler_fixtures_satisfy_common_schema(self) -> None:
        fixtures = [
            self.build_fixture(
                dataset="ce",
                source_id="2400536:1234",
                source_site="https://ce.pknu.ac.kr",
                document_type="notice",
                date="2026-09-02",
            ),
            self.build_fixture(
                dataset="pknu_notice",
                source_id="123456",
                source_site="https://www.pknu.ac.kr",
                document_type="notice",
                date="2026-09-02",
            ),
            self.build_fixture(
                dataset="pknu_student_life",
                source_id="guide:media:987",
                source_site="https://www.pknu.ac.kr",
                document_type="guide",
                date="2026-01-01",
                metadata={"published_at_precision": "year", "published_at_inferred": True},
            ),
            self.build_fixture(
                dataset="pknu_rule",
                source_id="law:42",
                source_site="https://www.law.go.kr",
                document_type="law",
                date="2026-09-02",
                metadata={"source_type": "hak"},
            ),
        ]
        for doc in fixtures:
            with self.subTest(dataset=doc["source_dataset"]):
                self.assertEqual(validate_common_document(doc), [])
                self.assertTrue(doc["legacy_only"])

    def test_id_and_slug_do_not_depend_on_title(self) -> None:
        first = self.build_fixture(
            dataset="pknu_notice",
            source_id="123456",
            source_site="https://www.pknu.ac.kr",
            document_type="notice",
            date="2026-09-02",
        )
        second = dict(first)
        second["title"] = "changed title"
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(first["slug"], second["slug"])
        self.assertEqual(first["slug"], document_slug("pknu_notice", "123456"))

    def test_content_hash_uses_normalized_content(self) -> None:
        self.assertEqual(
            content_sha256("첫 줄  \r\n둘째 줄"),
            content_sha256("첫 줄\n둘째 줄"),
        )

    def test_dates_are_timezone_aware_kst(self) -> None:
        doc = self.build_fixture(
            dataset="ce",
            source_id="board:1",
            source_site="https://ce.pknu.ac.kr",
            document_type="notice",
            date="2026-09-02",
        )
        self.assertEqual(doc["published_at"], "2026-09-02T00:00:00+09:00")
        self.assertTrue(doc["crawl"]["crawled_at"].endswith("+09:00"))
        self.assertEqual(doc["metadata"]["published_at_precision"], "date")
        self.assertTrue(doc["metadata"]["published_at_inferred"])

    def test_validator_rejects_invalid_timestamp_and_attachment_types(self) -> None:
        doc = self.build_fixture(
            dataset="ce", source_id="board:2", source_site="https://ce.pknu.ac.kr",
            document_type="notice", date="2026-09-02",
        )
        doc["published_at"] = "not-a-date+09:00"
        doc["attachments"] = [{
            "id": "attachment-001", "name": "x.pdf", "url": "https://example.test/x.pdf",
            "final_url": None, "saved_path": None, "downloaded": "true",
            "content_type": None, "size_bytes": "1", "sha256": None,
            "text": {"status": "success", "content": "x", "characters": "1", "extractor": "fixture", "error": None},
            "error": None,
        }]
        errors = validate_common_document(doc)
        self.assertTrue(any("published_at must be valid ISO 8601" in error for error in errors))
        self.assertTrue(any("downloaded must be boolean" in error for error in errors))
        self.assertTrue(any("size_bytes must be an integer" in error for error in errors))
        self.assertTrue(any("text.characters must be an integer" in error for error in errors))


if __name__ == "__main__":
    unittest.main()

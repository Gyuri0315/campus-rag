from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from scripts.crawlers.common.schema import (
    attachment_error,
    attachment_text,
    build_attachment,
    normalize_attachment,
    sanitize_attachment_filename,
    unique_attachment_filename,
    validate_attachment,
)
from scripts.rule.preprocessing import extract_rule_json_blocks

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]


def workspace_tempdir() -> tempfile.TemporaryDirectory[str]:
    return tempfile.TemporaryDirectory(dir=WORKSPACE_ROOT)


class AttachmentSchemaTests(unittest.TestCase):
    def test_download_success_has_relative_path_size_and_sha256(self) -> None:
        with workspace_tempdir() as temp_dir:
            root = Path(temp_dir)
            saved = root / "files" / "guide.pdf"
            saved.parent.mkdir(parents=True)
            payload = b"fixture-pdf"
            saved.write_bytes(payload)

            attachment = build_attachment(
                index=1,
                name="guide.pdf",
                url="https://example.test/guide.pdf",
                final_url="https://cdn.example.test/guide.pdf",
                saved_path=saved,
                downloaded=True,
                project_root=root,
                content_type="application/pdf; charset=binary",
            )

            self.assertEqual(validate_attachment(attachment, root), [])
            self.assertEqual(attachment["saved_path"], "files/guide.pdf")
            self.assertEqual(attachment["size_bytes"], len(payload))
            self.assertEqual(attachment["sha256"], hashlib.sha256(payload).hexdigest())
            self.assertEqual(attachment["final_url"], "https://cdn.example.test/guide.pdf")
            self.assertEqual(attachment["downloaded_from_url"], attachment["final_url"])

    def test_http_failure_keeps_attachment_with_structured_error(self) -> None:
        with workspace_tempdir() as temp_dir:
            root = Path(temp_dir)
            attachment = build_attachment(
                index=2,
                name="failed.hwp",
                url="https://example.test/failed.hwp",
                final_url="https://example.test/failed.hwp",
                project_root=root,
                error=attachment_error("HTTP_503", "service unavailable", True),
            )
            self.assertEqual(validate_attachment(attachment, root), [])
            self.assertFalse(attachment["downloaded"])
            self.assertIsNone(attachment["saved_path"])
            self.assertEqual(attachment["error"]["code"], "HTTP_503")
            self.assertTrue(attachment["error"]["retryable"])

    def test_empty_file_is_valid_download_with_zero_size(self) -> None:
        with workspace_tempdir() as temp_dir:
            root = Path(temp_dir)
            saved = root / "empty.pdf"
            saved.write_bytes(b"")
            attachment = build_attachment(
                index=1,
                name=saved.name,
                url="https://example.test/empty.pdf",
                saved_path=saved,
                downloaded=True,
                project_root=root,
                text=attachment_text("skipped", extractor="fixture", error="empty file"),
            )
            self.assertEqual(validate_attachment(attachment, root), [])
            self.assertEqual(attachment["size_bytes"], 0)
            self.assertEqual(attachment["text"]["status"], "skipped")

    def test_unsupported_extension_and_extraction_failure_are_typed(self) -> None:
        skipped = attachment_text(
            "skipped", characters=0, extractor="fixture", error="unsupported extension: .rar"
        )
        failed = attachment_text("failed", extractor="fixture", error="parser crashed")
        self.assertEqual(skipped["status"], "skipped")
        self.assertIsInstance(skipped["characters"], int)
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["error"], "parser crashed")

    def test_text_extraction_success_is_json_serializable(self) -> None:
        with workspace_tempdir() as temp_dir:
            root = Path(temp_dir)
            saved = root / "rule.hwp"
            saved.write_bytes(b"rule")
            attachment = build_attachment(
                index=1,
                name="rule.hwp",
                url="https://example.test/rule.hwp",
                saved_path=saved,
                downloaded=True,
                project_root=root,
                text=attachment_text(
                    "success", content="extracted rule", extractor="fixture"
                ),
            )
            encoded = json.dumps(attachment, ensure_ascii=False)
            decoded = json.loads(encoded)
            self.assertEqual(decoded["text"]["characters"], len("extracted rule"))
            self.assertEqual(validate_attachment(decoded, root), [])

    def test_filename_sanitizing_and_collision_resolution_are_shared(self) -> None:
        self.assertEqual(sanitize_attachment_filename("CON.txt"), "_CON.txt")
        self.assertEqual(sanitize_attachment_filename('bad<>:"name?.pdf'), "bad____name_.pdf")
        used: set[str] = set()
        self.assertEqual(unique_attachment_filename("Report.PDF", used), "Report.PDF")
        self.assertEqual(unique_attachment_filename("report.pdf", used), "report_2.pdf")

    def test_legacy_attachment_is_upgraded_without_losing_legacy_fields(self) -> None:
        with workspace_tempdir() as temp_dir:
            root = Path(temp_dir)
            saved = root / "legacy.pdf"
            saved.write_bytes(b"legacy")
            upgraded = normalize_attachment(
                {
                    "name": "legacy.pdf",
                    "url": "https://example.test/legacy.pdf",
                    "saved_path": saved.as_posix(),
                    "downloaded_from_url": "https://cdn.example.test/legacy.pdf",
                    "source_page_url": "https://example.test/post/1",
                },
                index=3,
                project_root=root,
            )
            self.assertEqual(validate_attachment(upgraded, root), [])
            self.assertEqual(upgraded["id"], "attachment-003")
            self.assertEqual(upgraded["source_page_url"], "https://example.test/post/1")

    def test_rule_preprocessing_prefers_canonical_attachment_text(self) -> None:
        doc = {
            "title": "rule",
            "category": "pknu_rule_law",
            "subcategory": "regulation",
            "type": "regulation",
            "date": "2026-09-02",
            "source_id": "law:1",
            "url": "https://example.test/rule/1",
            "html_text": "body text long enough to avoid requiring an HTML fallback",
            "attachments": [
                {
                    "name": "rule.pdf",
                    "url": "https://example.test/rule.pdf",
                    "saved_path": "files/rule.pdf",
                    "text": attachment_text(
                        "success", content="canonical attachment text", extractor="fixture"
                    ),
                }
            ],
            "file_preview_texts": [{"text": "legacy attachment text"}],
        }
        with workspace_tempdir() as temp_dir:
            blocks, _ = extract_rule_json_blocks(doc, Path(temp_dir))
        block_texts = [block["text"] for block in blocks]
        self.assertIn("canonical attachment text", block_texts)
        self.assertNotIn("legacy attachment text", block_texts)


if __name__ == "__main__":
    unittest.main()

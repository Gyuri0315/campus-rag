from __future__ import annotations

import unittest

from app.routers.ask import _row_to_source


class SourceAttachmentTests(unittest.TestCase):
    def test_maps_all_metadata_attachments_and_deduplicates_urls(self) -> None:
        source = _row_to_source(
            {
                "content": "source text",
                "metadata": {
                    "doc_title": "notice",
                    "doc_url": "https://example.com/notice",
                    "attachments": [
                        {"name": "guide.pdf", "url": "https://example.com/guide.pdf"},
                        {"name": "form.hwp", "url": "https://example.com/form.hwp"},
                    ],
                    "attachment_name": "duplicate",
                    "attachment_url": "https://example.com/guide.pdf",
                },
            }
        )

        self.assertEqual(
            [(item.name, item.url) for item in source.attachments],
            [
                ("guide.pdf", "https://example.com/guide.pdf"),
                ("form.hwp", "https://example.com/form.hwp"),
            ],
        )

    def test_maps_a_single_attachment_chunk(self) -> None:
        source = _row_to_source(
            {
                "content": "attachment text",
                "metadata": {
                    "source_file": "rules.pdf",
                    "attachment_name": "rules.pdf",
                    "attachment_url": "https://example.com/rules.pdf",
                },
            }
        )

        self.assertEqual(len(source.attachments), 1)
        self.assertEqual(source.attachments[0].name, "rules.pdf")


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import io
import json
import logging
import tempfile
import unittest
from pathlib import Path

from scripts.crawlers.common.logging import configure_crawler_logging, log_event, set_run_id


WORKSPACE_ROOT = Path(__file__).resolve().parents[2]


class CrawlerLoggingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=WORKSPACE_ROOT)
        self.root = Path(self.temp.name)
        self.console = io.StringIO()
        self.logger, self.context = configure_crawler_logging(
            "test_dataset", self.root, console_stream=self.console
        )
        set_run_id(self.context, "test_dataset-20260902T153012")

    def tearDown(self) -> None:
        for handler in list(self.logger.handlers):
            handler.close()
            self.logger.removeHandler(handler)
        self.temp.cleanup()

    def json_lines(self) -> list[dict[str, object]]:
        for handler in self.logger.handlers:
            handler.flush()
        path = self.root / "logs" / "crawlers" / "test_dataset.jsonl"
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def test_console_and_jsonl_required_fields(self) -> None:
        log_event(self.logger, logging.INFO, "document_saved", source_id="123", status="new")
        console = self.console.getvalue().strip()
        self.assertRegex(console, r"^\d{4}-\d{2}-\d{2}T.*\+09:00 INFO test_dataset document_saved ")
        payload = self.json_lines()[0]
        for field in ("timestamp", "level", "dataset", "run_id", "event"):
            self.assertIn(field, payload)
        self.assertEqual("123", payload["source_id"])

    def test_sensitive_context_and_url_query_are_masked(self) -> None:
        log_event(
            self.logger, logging.ERROR, "request_failed",
            url="https://example.test/path?token=secret-value&page=2",
            authorization="Bearer abc", headers={"Cookie": "session=abc"},
        )
        serialized = json.dumps(self.json_lines()[0], ensure_ascii=False)
        self.assertNotIn("secret-value", serialized)
        self.assertNotIn("Bearer abc", serialized)
        self.assertNotIn("session=abc", serialized)
        self.assertIn("REDACTED", serialized)

    def test_sensitive_key_variants_and_url_credentials_are_masked(self) -> None:
        log_event(
            self.logger, logging.ERROR, "request_failed",
            url="https://user:password@example.test/path?authToken=abc&client_secret=def",
            oauthAccessToken="ghi",
        )
        serialized = json.dumps(self.json_lines()[0], ensure_ascii=False)
        for secret in ("user", "password", "abc", "def", "ghi"):
            self.assertNotIn(secret, serialized)
        self.assertIn("REDACTED", serialized)

    def test_exception_is_recorded_at_error_level(self) -> None:
        try:
            raise RuntimeError("processing failed")
        except RuntimeError:
            log_event(self.logger, logging.ERROR, "document_failed", exc_info=True, source_id="x")
        payload = self.json_lines()[0]
        self.assertIn("RuntimeError: processing failed", str(payload["exception"]))

    def test_tls_fallback_event_is_structured(self) -> None:
        log_event(
            self.logger, logging.WARNING, "tls_fallback_used",
            url="https://fishsci.pknu.ac.kr/kor/view.do?no=44",
            policy="windows_system_ca",
        )
        payload = self.json_lines()[0]
        self.assertEqual("tls_fallback_used", payload["event"])
        self.assertEqual("windows_system_ca", payload["policy"])

    def test_reconfiguration_does_not_duplicate_handlers(self) -> None:
        second_console = io.StringIO()
        logger, _ = configure_crawler_logging("test_dataset", self.root, console_stream=second_console)
        self.assertEqual(3, len(logger.handlers))
        log_event(logger, logging.INFO, "run_started", mode="incremental")
        self.assertEqual(1, len(second_console.getvalue().splitlines()))


if __name__ == "__main__":
    unittest.main()

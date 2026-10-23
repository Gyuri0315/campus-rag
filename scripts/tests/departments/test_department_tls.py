from __future__ import annotations

import unittest
import sys
import tempfile
import ssl
from dataclasses import replace
from unittest.mock import Mock, patch

import requests

from scripts.crawlers.departments import cli, engine
from scripts.crawlers.departments.probe import probe_site
from scripts.crawlers.departments.config import get_department
from scripts.crawlers.departments.tls import NETWORK_REQUEST_FAILED, TLS_CERTIFICATE_VERIFY_FAILED, classify_request_error, get_with_tls_policy


class DepartmentTlsTests(unittest.TestCase):
    def test_single_dataset_probe_starts_with_registry_https(self) -> None:
        probe = Mock(
            status="unsupported", compatible=False,
            to_dict=lambda: {"status": "unsupported"},
        )
        with tempfile.TemporaryDirectory() as directory:
            argv = [
                "cli.py", "probe", "--dataset", "mpsm",
                "--output-root", directory,
            ]
            with patch.object(sys, "argv", argv), patch.object(cli, "probe_site", return_value=probe) as call:
                self.assertEqual(0, cli.main())
        self.assertEqual("https://mpsm.pknu.ac.kr", call.call_args.kwargs["url"])
        self.assertTrue(call.call_args.kwargs["system_trust_fallback"])

    def test_certificate_error_is_distinct_from_network_error(self) -> None:
        cert = requests.exceptions.SSLError("CERTIFICATE_VERIFY_FAILED: unable to get issuer")
        network = requests.exceptions.ConnectionError("connection refused")
        self.assertEqual(TLS_CERTIFICATE_VERIFY_FAILED, classify_request_error(cert))
        self.assertEqual(NETWORK_REQUEST_FAILED, classify_request_error(network))

    def test_fallback_is_not_used_for_general_network_failure(self) -> None:
        client = Mock()
        client.get.side_effect = requests.exceptions.ConnectionError("connection refused")
        with patch("scripts.crawlers.departments.tls.build_system_trust_session") as build:
            with self.assertRaises(requests.exceptions.ConnectionError):
                get_with_tls_policy(client, "https://example.test", system_trust_fallback=True)
        build.assert_not_called()

    def test_certificate_fallback_requires_explicit_policy(self) -> None:
        client = Mock()
        client.get.side_effect = requests.exceptions.SSLError("CERTIFICATE_VERIFY_FAILED")
        with patch("scripts.crawlers.departments.tls.build_system_trust_session") as build:
            with self.assertRaises(requests.exceptions.SSLError):
                get_with_tls_policy(client, "https://example.test")
        build.assert_not_called()

    def test_enabled_fallback_keeps_verification_and_returns_response(self) -> None:
        client = Mock()
        client.get.side_effect = requests.exceptions.SSLError("CERTIFICATE_VERIFY_FAILED")
        response = Mock(status_code=200, url="https://example.test", text="<main>ok</main>")
        fallback = Mock()
        fallback.get.return_value = response
        with patch("scripts.crawlers.departments.tls.build_system_trust_session", return_value=fallback):
            actual, used = get_with_tls_policy(
                client, "https://example.test", system_trust_fallback=True, timeout=3,
            )
        self.assertIs(response, actual)
        self.assertTrue(used)
        self.assertNotIn("verify", fallback.get.call_args.kwargs)

    def test_fallback_cookies_are_returned_to_the_source_session(self) -> None:
        client = Mock()
        client.cookies = requests.cookies.RequestsCookieJar()
        client.get.side_effect = requests.exceptions.SSLError("CERTIFICATE_VERIFY_FAILED")
        response = Mock(status_code=200)
        fallback = Mock()
        fallback.cookies = requests.cookies.RequestsCookieJar()
        fallback.cookies.set("session", "verified")
        fallback.get.return_value = response
        with patch("scripts.crawlers.departments.tls.build_system_trust_session", return_value=fallback):
            get_with_tls_policy(client, "https://example.test", system_trust_fallback=True)
        self.assertEqual("verified", client.cookies.get("session"))

    def test_probe_reports_typed_failure_without_exposing_context(self) -> None:
        client = Mock()
        client.get.side_effect = requests.exceptions.SSLError("CERTIFICATE_VERIFY_FAILED")
        result = probe_site(site_key="safe.test", url="https://safe.test", session=client)
        self.assertEqual(TLS_CERTIFICATE_VERIFY_FAILED, result.error["code"])
        self.assertNotIn("token", result.to_dict())
        self.assertNotIn("cookie", result.to_dict())


    def test_common_engine_does_not_disable_certificate_verification(self) -> None:
        session = engine.build_session()
        self.assertIsNot(session.verify, False)

    def test_opt_in_legacy_negotiation_retains_certificate_and_host_verification(self) -> None:
        config = replace(get_department("ce"), base_url="https://legacy.example.test/site",
                         tls_legacy_server_connect=True, tls_legacy_server_hosts=("assets.example.test",))
        with patch.object(engine, "ACTIVE_CONFIG", config):
            session = engine.build_session()
        adapter = session.get_adapter("https://legacy.example.test/site/1")
        self.assertTrue(adapter._context.check_hostname)
        self.assertEqual(adapter._context.verify_mode, ssl.CERT_REQUIRED)
        self.assertIsNot(session.verify, False)
        self.assertIsNot(adapter, session.get_adapter("https://other.example.test/1"))
        assets = session.get_adapter("https://assets.example.test/image.png")
        self.assertTrue(assets._context.check_hostname)
        self.assertEqual(assets._context.verify_mode, ssl.CERT_REQUIRED)


if __name__ == "__main__":
    unittest.main()

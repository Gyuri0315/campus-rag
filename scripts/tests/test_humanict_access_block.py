from pathlib import Path
import unittest

import requests

from scripts.crawlers.departments.access import ACCESS_BLOCKED, detect_access_block
from scripts.crawlers.departments.config import load_registry
from scripts.crawlers.departments.discovery import discover_site
from scripts.crawlers.departments.probe import analyze_site_html, probe_site
from scripts.crawlers.departments.tls import (
    NETWORK_REQUEST_FAILED, TLS_CERTIFICATE_VERIFY_FAILED, classify_request_error,
)


FIX = Path(__file__).parent / "fixtures" / "departments" / "humanict"
REQUESTED = "https://humanict.pknu.ac.kr/"
DENIED = "https://humanict.pknu.ac.kr/common/deny.jsp"


def response(url: str, html: str) -> requests.Response:
    item = requests.Response()
    item.status_code = 200
    item.url = url
    item.encoding = "utf-8"
    item._content = html.encode("utf-8")
    return item


class FakeSession(requests.Session):
    def __init__(self, item: requests.Response):
        super().__init__()
        self.item = item
        self.calls = 0

    def get(self, url, **kwargs):
        self.calls += 1
        return self.item


class HumanictAccessBlockTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.denied_html = (FIX / "deny.html").read_text(encoding="utf-8")
        cls.ordinary_html = (FIX / "ordinary.html").read_text(encoding="utf-8")

    def test_final_url_title_and_body_are_independent_evidence(self):
        evidence = detect_access_block(final_url=DENIED, html=self.denied_html)
        self.assertTrue(evidence.blocked)
        self.assertEqual(("deny_url", "deny_title", "deny_body"), evidence.reasons)
        self.assertTrue(detect_access_block(final_url=DENIED, html=self.ordinary_html).blocked)
        self.assertFalse(detect_access_block(final_url=REQUESTED, html=self.ordinary_html).blocked)

    def test_http_200_denial_is_blocked_in_probe_and_discovery(self):
        probe = analyze_site_html(site_key="humanict", requested_url=REQUESTED,
                                  final_url=DENIED, html=self.denied_html, http_status=200)
        self.assertEqual("blocked", probe.status)
        self.assertFalse(probe.compatible)
        self.assertEqual(ACCESS_BLOCKED, probe.error["code"])
        self.assertFalse(probe.error["retryable"])
        session = FakeSession(response(DENIED, self.denied_html))
        live_probe = probe_site(site_key="humanict", url=REQUESTED, session=session)
        self.assertEqual("blocked", live_probe.status)
        discovery = discover_site(site_key="humanict", base_url=REQUESTED, session=session)
        self.assertEqual("blocked", discovery.status)
        self.assertEqual(ACCESS_BLOCKED, discovery.errors[0]["code"])
        self.assertFalse(discovery.errors[0]["retryable"])

    def test_network_tls_and_access_block_are_distinct(self):
        self.assertEqual(NETWORK_REQUEST_FAILED, classify_request_error(requests.ConnectionError("offline")))
        self.assertEqual(TLS_CERTIFICATE_VERIFY_FAILED,
                         classify_request_error(requests.exceptions.SSLError("CERTIFICATE_VERIFY_FAILED")))
        self.assertEqual(ACCESS_BLOCKED, ACCESS_BLOCKED)

    def test_registry_remains_disabled(self):
        config = load_registry()["humanict"]
        self.assertFalse(config.enabled)
        self.assertFalse(config.crawl_ready)


if __name__ == "__main__":
    unittest.main()

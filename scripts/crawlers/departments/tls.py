"""Narrow TLS policies for department sites without disabling verification."""

from __future__ import annotations

import ssl
from urllib.parse import urlsplit

import requests
from requests.adapters import HTTPAdapter


TLS_CERTIFICATE_VERIFY_FAILED = "TLS_CERTIFICATE_VERIFY_FAILED"
TLS_HANDSHAKE_FAILED = "TLS_HANDSHAKE_FAILED"
NETWORK_REQUEST_FAILED = "NETWORK_REQUEST_FAILED"


def classify_request_error(exc: requests.RequestException) -> str:
    if isinstance(exc, requests.exceptions.SSLError):
        text = str(exc).upper()
        if "CERTIFICATE_VERIFY_FAILED" in text or "CERTIFICATE VERIFY FAILED" in text:
            return TLS_CERTIFICATE_VERIFY_FAILED
        return TLS_HANDSHAKE_FAILED
    return NETWORK_REQUEST_FAILED


class _SSLContextAdapter(HTTPAdapter):
    def __init__(self, context: ssl.SSLContext) -> None:
        self._context = context
        super().__init__()

    def init_poolmanager(self, *args, **kwargs):
        kwargs["ssl_context"] = self._context
        return super().init_poolmanager(*args, **kwargs)

    def proxy_manager_for(self, proxy, **kwargs):
        kwargs["ssl_context"] = self._context
        return super().proxy_manager_for(proxy, **kwargs)


def build_system_trust_session(source: requests.Session, url: str) -> requests.Session:
    """Build a verified session using Windows ROOT certificates plus defaults."""
    if not hasattr(ssl, "enum_certificates"):
        raise RuntimeError("system trust fallback is only available with a system certificate API")
    context = ssl.create_default_context()
    certificates = ssl.enum_certificates("ROOT")
    pem = "".join(
        ssl.DER_cert_to_PEM_cert(cert)
        for cert, encoding, _trust in certificates
        if encoding == "x509_asn"
    )
    if not pem:
        raise RuntimeError("system trust store contains no usable X.509 certificates")
    context.load_verify_locations(cadata=pem)
    fallback = requests.Session()
    fallback.headers.update(source.headers)
    fallback.cookies.update(source.cookies)
    host = urlsplit(url).hostname
    if not host:
        raise ValueError("TLS fallback requires an absolute URL")
    fallback.mount(f"https://{host}/", _SSLContextAdapter(context))
    return fallback


def get_with_tls_policy(
    client: requests.Session, url: str, *, system_trust_fallback: bool = False, **kwargs,
) -> tuple[requests.Response, bool]:
    """GET normally, retrying only certificate verification failures with system CAs."""
    try:
        return client.get(url, **kwargs), False
    except requests.exceptions.SSLError as exc:
        if classify_request_error(exc) != TLS_CERTIFICATE_VERIFY_FAILED or not system_trust_fallback:
            raise
        fallback = build_system_trust_session(client, url)
        response = fallback.get(url, **kwargs)
        client.cookies.update(fallback.cookies)
        return response, True

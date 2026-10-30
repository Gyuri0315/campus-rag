"""Narrow TLS policies for department sites without disabling verification."""

from __future__ import annotations

import ssl
import json
from pathlib import Path
from urllib.parse import urlsplit

import requests
from requests.adapters import HTTPAdapter


TLS_CERTIFICATE_VERIFY_FAILED = "TLS_CERTIFICATE_VERIFY_FAILED"
TLS_HANDSHAKE_FAILED = "TLS_HANDSHAKE_FAILED"
NETWORK_REQUEST_FAILED = "NETWORK_REQUEST_FAILED"

PKNU_INTERMEDIATES = Path(__file__).resolve().parents[1] / "certificates" / "pknu-intermediates.pem"
VERIFIED_HOSTS_FILE = PKNU_INTERMEDIATES.with_name("department-tls-hosts.json")
# Exact hosts only; False keeps the default ciphers and adds the intermediate.
VERIFIED_PKNU_HOSTS = json.loads(VERIFIED_HOSTS_FILE.read_text(encoding="utf-8"))["hosts"]


def mount_verified_pknu_tls(session: requests.Session) -> None:
    """Apply verified policies to exact HTTPS hosts, including redirect targets."""
    contexts = {}
    for host, legacy_ciphers in VERIFIED_PKNU_HOSTS.items():
        if legacy_ciphers not in contexts:
            context = ssl.create_default_context()
            context.load_verify_locations(cafile=str(PKNU_INTERMEDIATES))
            if legacy_ciphers:
                context.set_ciphers("DEFAULT@SECLEVEL=1")
            contexts[legacy_ciphers] = context
        session.mount(f"https://{host}/", _SSLContextAdapter(contexts[legacy_ciphers]))
    session._pknu_tls_mounted = True


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


def mount_verified_legacy_tls(session: requests.Session, url: str) -> None:
    """Opt in to legacy server negotiation on one HTTPS host, retaining verification."""
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("legacy TLS policy requires an HTTPS site URL")
    context = ssl.create_default_context()
    context.set_ciphers("DEFAULT@SECLEVEL=1")
    if hasattr(ssl, "OP_LEGACY_SERVER_CONNECT"):
        context.options |= ssl.OP_LEGACY_SERVER_CONNECT
    session.mount(f"https://{parsed.netloc}/", _SSLContextAdapter(context))


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
    if host in VERIFIED_PKNU_HOSTS:
        context.load_verify_locations(cafile=str(PKNU_INTERMEDIATES))
        if VERIFIED_PKNU_HOSTS[host]:
            context.set_ciphers("DEFAULT@SECLEVEL=1")
    fallback.mount(f"https://{host}/", _SSLContextAdapter(context))
    return fallback


def get_with_tls_policy(
    client: requests.Session, url: str, *, system_trust_fallback: bool = False, **kwargs,
) -> tuple[requests.Response, bool]:
    """GET normally, retrying only certificate verification failures with system CAs."""
    # Probe/discovery sessions also need the same exact-host policies. Avoid
    # replacing an already configured adapter (and its connection pools).
    if isinstance(client, requests.Session) and not getattr(client, "_pknu_tls_mounted", False):
        mount_verified_pknu_tls(client)
        client._pknu_tls_mounted = True
    try:
        return client.get(url, **kwargs), False
    except requests.exceptions.SSLError as exc:
        if classify_request_error(exc) != TLS_CERTIFICATE_VERIFY_FAILED or not system_trust_fallback:
            raise
        fallback = build_system_trust_session(client, url)
        response = fallback.get(url, **kwargs)
        client.cookies.update(fallback.cookies)
        return response, True

"""Quick ebook PDF probe."""
import re
import ssl
from urllib.parse import urljoin

import requests
from requests.adapters import HTTPAdapter

BASE = "https://www.pknu.ac.kr"
EBOOK_BASE = f"{BASE}/ebook/col_life/kor/"


class A(HTTPAdapter):
    def init_poolmanager(self, *args, **kwargs):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        ctx.set_ciphers("DEFAULT@SECLEVEL=1")
        if hasattr(ssl, "OP_LEGACY_SERVER_CONNECT"):
            ctx.options |= ssl.OP_LEGACY_SERVER_CONNECT
        kwargs["ssl_context"] = ctx
        super().init_poolmanager(*args, **kwargs)


def main() -> None:
    s = requests.Session()
    s.verify = False
    s.mount("https://", A())
    cfg_url = urljoin(EBOOK_BASE, "mobile/javascript/config.js")
    r = s.get(cfg_url, timeout=30)
    print("config.js", r.status_code, len(r.content))
    text = r.text
    pdfs = sorted(set(re.findall(r'["\']([^"\']*\.pdf)["\']', text, re.I)))
    print("quoted pdfs in config:", pdfs[:20])
    for m in re.finditer(r"downloadURL[^;]{0,200}", text, re.I):
        print("downloadURL snippet:", m.group()[:200])
    for rel in [
        "files/col_life.pdf",
        "files/source.pdf",
        "files/mobile/col_life.pdf",
        "download/col_life.pdf",
        "files/publication.pdf",
    ]:
        u = urljoin(EBOOK_BASE, rel)
        try:
            h = s.head(u, timeout=10, allow_redirects=True)
            ct = h.headers.get("Content-Type", "")
            print("HEAD", rel, h.status_code, ct)
        except Exception as exc:
            print("HEAD", rel, exc)


if __name__ == "__main__":
    main()

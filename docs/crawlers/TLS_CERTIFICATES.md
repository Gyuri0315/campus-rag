# PKNU intermediate certificate

Certificate assets remain in [`scripts/crawlers/certificates/`](../../scripts/crawlers/certificates).

[`pknu-intermediates.pem`](../../scripts/crawlers/certificates/pknu-intermediates.pem) supplies the intermediate CA certificate omitted by
`www.pknu.ac.kr` and several department hosts. The rule crawler uses it for
`https://www.pknu.ac.kr/`. The department engine also loads it for these exact
HTTPS hosts, verified on 2026-10-25. The complete exact-host allowlist is in
[department-tls-hosts.json](../../scripts/crawlers/certificates/department-tls-hosts.json). Its boolean values select
`DEFAULT@SECLEVEL=1` when true and default ciphers when false; both load the
existing intermediate and retain the default trust roots. Hosts that passed
with default TLS are not added. `icms.pknu.ac.kr`, used by several majors after
redirects, was verified independently and is explicitly included.

On 2026-10-28, attachment recovery additionally verified and registered
`cms.pknu.ac.kr`, `www.pknu.ac.kr`, and `libweb.pknu.ac.kr`. These hosts required
the existing intermediate and `DEFAULT@SECLEVEL=1`; certificate-chain and
hostname checks remained enabled. The manifest records this additional check
separately from the original verification date.

All retain certificate-chain and hostname verification. Policies do not cover
subdomains, other ports, or other redirect destinations. Existing explicit
registry TLS options are retained. New hosts require their own connection
verification before inclusion in `department-tls-hosts.json`. Receiving an HTTP
response validates TLS connectivity, not content access: `humanict` redirects
to an access-denied page and remains recorded as blocked in the crawler registry.

- Subject: Sectigo Public Server Authentication CA DV R36
- Issuer: Sectigo Public Server Authentication Root R46
- Validity: 2021-03-22 through 2036-03-21 (UTC)
- Source: http://crt.sectigo.com/SectigoPublicServerAuthenticationCADVR36.crt
- SHA-256 of DER certificate: `8c54c334b66ba4e426772af4a3f9136c19a1aec729fdb28c535c07a5a4ef22e0`
- Verified on: 2026-10-25

The source is the CA Issuers URL in the server's certificate. The downloaded
intermediate was accepted only after the PKNU connection passed certificate
chain validation to existing trusted roots and hostname validation. The PEM
contains no server leaf certificate or additional root certificate.

Python's default/system CA store is retained, and Requests can still load its
certifi roots or a `REQUESTS_CA_BUNDLE`. No OS trust store is modified. No CA
download is required during crawling.

If PKNU changes its issuing CA, obtain the new intermediate from the issuer,
validate the complete chain against existing trusted roots, and update this
file and its metadata. Do not add a server leaf certificate or disable
certificate/hostname checks to work around a validation failure.

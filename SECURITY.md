# Security Policy

## Reporting a vulnerability

Please **do not open a public issue** for security problems.

Use GitHub's private vulnerability reporting: **Security → Report a
vulnerability** on this repository. You'll get an acknowledgement within a
few days. Coordinated disclosure is appreciated — give us a reasonable window
to ship a fix before publishing details.

## Supported versions

Only the latest release (`main`) is supported with security fixes.

## Threat model & hardening

Anja Hub is designed for **self-hosting by a single person or a small team**,
typically bound to `127.0.0.1` or behind a VPN/reverse-proxy with TLS. It is
not designed to be exposed raw to the public internet.

Existing defenses (validated by the 25-check suite in
`anja-hub/tests/test_security_gates.py`):

- authorization gates on all mutating endpoints (owner/admin roles,
  per-workspace membership, fail-closed middleware in multi-user mode)
- CSRF origin checks, CSP / X-Frame-Options / nosniff headers
- SSRF guard with DNS-rebinding IP pinning on outbound fetches
- path-traversal defenses on all file-serving endpoints
- rate-limited login, session HMAC cookies (httponly, secure on TLS)
- secrets in an encrypted (Fernet) vault, never exposed by the API

## Deployment recommendations

- Keep the server bound to localhost or a private network (WireGuard/VPN).
- Run behind TLS if remote (`ANJA_COOKIE_SECURE=1`).
- Use multi-user (Concierge) mode if more than one person has access.
- Back up your hub directory (`backup.py`) — it contains your vault and data.

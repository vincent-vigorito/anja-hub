# Contributing to Anja Hub

Thanks for your interest! Contributions of every size are welcome — bug
reports, docs, translations, features.

## Developer Certificate of Origin (DCO)

All commits **must** be signed off:

```bash
git commit -s -m "fix: ..."
```

The `-s` flag adds a `Signed-off-by: Your Name <you@example.com>` trailer,
certifying you wrote the change (or have the right to submit it) under the
project's MIT license, per the [Developer Certificate of
Origin](https://developercertificate.org). PRs with unsigned commits cannot
be merged.

## Dev setup

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r anja-hub/webapp/requirements.txt
python3 anja-hub/scripts/init_hub.py --target ~/anja-dev-hub
python3 anja-hub/webapp/server.py --hub ~/anja-dev-hub --port 8765
```

UI changes (`anja-hub/webapp/static/`) only need a browser reload; Python
changes need a server restart.

## Before opening a PR

```bash
# security gates — must stay 25/25 green
python3 anja-hub/tests/test_security_gates.py
# tests for the area you touched, e.g.:
python3 anja-hub/tests/merchant_test.py
node --check anja-hub/webapp/static/app.js
```

## Project conventions

- **Stdlib first**: only `anja-hub/webapp/` has external deps. MCP servers
  and scripts stay stdlib-only wherever possible.
- **No over-engineering**: three similar lines beat a premature abstraction.
  Targeted fixes, no surprise refactors.
- **Comments explain "why", not "what"** — well-named code documents itself.
- **Code vs data**: never write inside the hub data directory from the repo;
  everything user-owned lives in `<hub>/`.
- Commit messages: conventional-ish (`fix(scope): ...`, `feat(scope): ...`),
  Italian or English both fine.

## Reporting bugs

Open an issue with: what you did, what you expected, what happened, server
log excerpt (`ERROR`/`Traceback` lines), OS + Python version. Never paste
secrets or vault contents.

## Security issues

Do **not** open public issues for vulnerabilities — see
[SECURITY.md](SECURITY.md).

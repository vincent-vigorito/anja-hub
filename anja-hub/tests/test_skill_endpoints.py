#!/usr/bin/env python3
"""test_skill_endpoints.py — Verifica che ogni endpoint citato nella SKILL.md
di `hub-admin` esista nel backend (server.py).

Estrae tutti i pattern `METHOD /api/path` dalla skill, li confronta con i
`@app.<method>("/path")` decorator del server, e fallisce se qualcosa manca.

Run: python3 test_skill_endpoints.py
Exit 0 = OK, 1 = drift detected.
"""

import re
import sys
from pathlib import Path


def main():
    repo = Path(__file__).resolve().parent.parent
    skill_md = repo / "skills" / "hub-admin" / "SKILL.md"
    server_py = repo / "webapp" / "server.py"

    if not skill_md.is_file():
        print(f"ERROR: skill not found at {skill_md}", file=sys.stderr); return 1
    if not server_py.is_file():
        print(f"ERROR: server.py not found at {server_py}", file=sys.stderr); return 1

    # Backend endpoints
    backend = set()
    for m in re.finditer(r'@app\.(get|post|patch|put|delete)\(["\'](/[^"\']*)["\']', server_py.read_text()):
        backend.add((m.group(1).upper(), m.group(2)))

    # Skill endpoints (METHOD /path; normalizza {...} placeholder ricorrenti)
    skill_text = skill_md.read_text()
    skill_eps = set()
    for m in re.finditer(r'\b(GET|POST|PATCH|PUT|DELETE)\s+(/[a-zA-Z0-9_/{}.\-]+)', skill_text):
        path = m.group(2)
        path = re.sub(r'\{\.\.\.\}', '{scope_kind}/{scope_target}/{goal_id}', path)
        # Strip query params se presenti negli esempi (es. ?which=...)
        path = path.split('?', 1)[0]
        skill_eps.add((m.group(1), path))

    missing = skill_eps - backend
    if missing:
        print("❌ DRIFT: endpoint citati nella skill ma assenti dal backend:")
        for method, path in sorted(missing):
            print(f"  - {method:6s} {path}")
        return 1

    print(f"✅ OK: {len(skill_eps)} endpoint citati nella skill, tutti presenti nel backend ({len(backend)} totali).")
    return 0


if __name__ == "__main__":
    sys.exit(main())

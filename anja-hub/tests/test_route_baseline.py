#!/usr/bin/env python3
"""test_route_baseline.py — Baseline di regressione per server.py.

Due check, pensati come rete di sicurezza per il futuro split del monolite
(server.py → router modulari) e per non re-introdurre path hardcoded:

1. ROUTE COUNT: il numero di route `@app.<method>(...)` non deve scendere sotto
   una soglia. Uno split che "perde" un gruppo di route lo fa fallire.
2. NO PATH HARDCODED: i file del runtime path principale non devono contenere
   assegnazioni `Path("/Users/...")` (machine-specific). Esclusi i default CLI
   con override env e gli script di migrazione one-shot.

Run: python3 test_route_baseline.py   (exit 0 = OK, 1 = regressione)
"""

import re
import sys
from pathlib import Path

WEBAPP = Path(__file__).resolve().parent.parent / "webapp"
SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
ROUTINES = Path(__file__).resolve().parent.parent.parent / "anja-routines" / "scripts"

# Baseline misurata al 2026-06-04 = 201 route HTTP/WS. Soglia con margine per refactor minori;
# un split che perde un intero gruppo (≥5 route) la fa scattare.
MIN_ROUTES = 195

# File nel path di esecuzione normale che NON devono avere Path literal hardcoded.
CORE_FILES = [
    WEBAPP / "server.py",
    WEBAPP / "workspace_scaffold.py",
    WEBAPP / "claude_chat.py",
    WEBAPP / "context_composer.py",
    SCRIPTS / "agent_add.py",
    SCRIPTS / "init_hub.py",
    ROUTINES / "runner.py",
]


def check_route_count() -> bool:
    server = WEBAPP / "server.py"
    n = len(re.findall(r"^@app\.(get|post|put|delete|patch|websocket)\(", server.read_text(), re.M))
    if n < MIN_ROUTES:
        print(f"❌ ROUTE COUNT: {n} route < soglia {MIN_ROUTES} — route perse in un refactor?")
        return False
    print(f"✓ route count: {n} (≥ {MIN_ROUTES})")
    return True


def check_no_hardcoded_paths() -> bool:
    ok = True
    for f in CORE_FILES:
        if not f.is_file():
            print(f"❌ file core mancante: {f}")
            ok = False
            continue
        for i, line in enumerate(f.read_text().splitlines(), 1):
            if re.search(r'Path\(\s*["\']/Users/', line):
                print(f"❌ path hardcoded in {f.name}:{i}: {line.strip()}")
                ok = False
    if ok:
        print(f"✓ nessun Path hardcoded nei {len(CORE_FILES)} file core")
    return ok


def main() -> int:
    results = [check_route_count(), check_no_hardcoded_paths()]
    if all(results):
        print("\n✅ baseline OK")
        return 0
    print("\n❌ baseline FALLITA")
    return 1


if __name__ == "__main__":
    sys.exit(main())
